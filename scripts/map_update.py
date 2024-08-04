#!/usr/bin/env python3

import datetime
import downloader
import itertools
import json
import logging
import os
import re
import shutil
import sys
import typing
import xml.etree.ElementTree

_TravellerMapUrl = 'https://www.travellermap.com'
_MapDataDir = 'map'
_MilieuDir = 'milieu'
_UniverseFileName = 'universe.json'
_SophontsFileName = 'sophonts.json'
_AllegiancesFileName = 'allegiances.json'
_MainsFileName = 'mains.json'
_DataFormatFileName = 'dataformat.txt'
_TimestampFileName = 'timestamp.txt'
_TimestampFormat = '%Y-%m-%d %H:%M:%S.%f'
_MilieuList = ['IW', 'M0', 'M990', 'M1105', 'M1120', 'M1201', 'M1248', 'M1900']
_MinMilieuFiles = 3 # Must have at least universe file and .sec and metadata files for 1 sector
_SectorTimestampPattern = re.compile('^#\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\s*$')
_DataFormatVersion = '4.0'

# List of characters that are illegal in filenames on Windows, Linux and macOS.
# Based on this post https://stackoverflow.com/questions/1976007/what-characters-are-forbidden-in-windows-and-linux-directory-names
_WindowsIllegalCharacters = set(['/', '<', '>', ':', '"', '\\', '|', '?', '*'])
_LinuxIllegalCharacters = set(['/'])
_MacOSIllegalCharacters = set(['/', ':'])

# This is the list of characters that are encoded/decoded to generate a filename that's valid on any
# filesystem. I've added % as filenames containing these characters will be percent escaped
_EncodedCharacters = set(itertools.chain(
    set(['%']),
    _WindowsIllegalCharacters,
    _LinuxIllegalCharacters,
    _MacOSIllegalCharacters))

def _bytesToString(bytes: bytes) -> str:
    return bytes.decode('utf-8-sig')

def _stringToBytes(string: str) -> bytes:
    return string.encode()

def _encodeFileName(rawFileName: str) -> str:
    escapedFileName = ''
    for index in range(0, len(rawFileName)):
        char = rawFileName[index]
        if char in _EncodedCharacters:
            char = f'%{format(ord(char), "x")}'
        escapedFileName += char
    return escapedFileName

# Remove timestamps from sector files downloaded from Traveller Map. This is needed as otherwise
# every sector file will be seen as modified every time the update is performed
def _removeTimestampFromSector(sectorData: str) -> typing.Optional[str]:
    linesRemoved = 0
    modifiedData = ''
    for line in sectorData.splitlines():
        if not _SectorTimestampPattern.search(line):
            modifiedData += line + '\n'
        else:
            linesRemoved += 1
    if linesRemoved != 1:
        return None

    return modifiedData

def _downloadMapData() -> None:
    fileRetriever = downloader.Downloader()
    basePath = os.path.join(os.getcwd(), _MapDataDir)
    downloadQueue = []

    # Delete old data directory to allow for sectors being deleted/renamed
    logging.info(f'Deleting existing map data')
    if os.path.exists(basePath):
        shutil.rmtree(basePath)
    else:
        logging.warning(f'No map data to delete')
    os.makedirs(basePath)

    logging.info(f'Downloading new map data')
    startTime = datetime.datetime.utcnow()

    sophontsUrl = f'{_TravellerMapUrl}/t5ss/sophonts'
    sophontsFilePath = os.path.join(basePath, _SophontsFileName)
    logging.info(f'Downloading sophonts file from {sophontsUrl} to {sophontsFilePath}')
    fileRetriever.downloadToFile(url=sophontsUrl, filePath=sophontsFilePath)

    allegiancesUrl = f'{_TravellerMapUrl}/t5ss/allegiances'
    allegiancesFilePath = os.path.join(basePath, _AllegiancesFileName)
    logging.info(f'Downloading allegiances file from {allegiancesUrl} to {allegiancesFilePath}')
    fileRetriever.downloadToFile(url=allegiancesUrl, filePath=allegiancesFilePath)

    mainsUrl = f'{_TravellerMapUrl}/res/mains.json'
    mainsFilePath = os.path.join(basePath, _MainsFileName)
    logging.info(f'Downloading mains file from {mainsUrl} to {mainsFilePath}')
    fileRetriever.downloadToFile(url=mainsUrl, filePath=mainsFilePath)

    for milieu in _MilieuList:
        universeUrl = f'{_TravellerMapUrl}/api/universe?milieu={milieu}&requireData=1'

        milieuDirPath = os.path.join(basePath, _MilieuDir, milieu)
        universeFilePath = os.path.join(milieuDirPath, _UniverseFileName)

        # If there was a milieu specified it means this is a universe file that was downloaded
        # so we want to add the sectors
        logging.info(f'Downloading {milieu} universe file from {universeUrl}')
        universeJson = fileRetriever.downloadToBuffer(url=universeUrl)
        universeJson = json.loads(_bytesToString(universeJson))
        if 'Sectors' not in universeJson:
            raise RuntimeError('Invalid sector list')

        # Check for name conflicts where there are multiple sectors with the same name.
        # At the time of writing the only instance of this is multiple sectors called
        # "Unnamed" in M1105
        # NOTE: It's important that this check is case insensitive as sector names will
        # be used as file names on Windows
        usedNames = set()
        conflictNames = set()
        for sectorInfo in universeJson['Sectors']:
            names = sectorInfo['Names']
            lowerName = str(names[0]['Text']).lower()
            if lowerName not in usedNames:
                usedNames.add(lowerName)
            else:
                conflictNames.add(lowerName)

        # Resolve any name conflicts
        nameMappings = {}
        if conflictNames:
            logging.info(f'Resolving name conflicts in {milieu}')
            for sectorInfo in universeJson['Sectors']:
                names = sectorInfo['Names']
                canonicalName = str(names[0]['Text'])
                sectorX = int(sectorInfo['X'])
                sectorY = int(sectorInfo['Y'])

                # Resolve name conflicts.
                # NOTE: Checking for conflicts is case insensitive
                if canonicalName.lower() in conflictNames:
                    logging.info(f'Disambiguating {milieu} sector named {canonicalName} at {sectorX}, {sectorY}')

                    unambiguousName = f'{canonicalName} ({sectorX}, {sectorY})'
                    if unambiguousName.lower() in usedNames:
                        # Realistically this shouldn't happen so don't try to do anything clever until
                        # we know that we actually need to handle it. This should fail the pipeline so
                        # I can take a look
                        raise RuntimeError(
                            f'Attempt to disambiguate {milieu} sector as {unambiguousName} failed as generated name is in use')

                    # Update the json structure so the new name is written to the snapshot
                    names[0]['Text'] = unambiguousName
                    nameMappings[unambiguousName] = canonicalName

        logging.info(f'Writing {milieu} universe file to {universeFilePath}')
        os.makedirs(milieuDirPath)
        with open(universeFilePath, 'w', encoding='utf-8') as file:
            json.dump(universeJson, file, separators=(',', ':')) # Specify separators to minimize white space

        logging.info(f'Downloading {milieu} sector & metadata files')
        for sectorInfo in universeJson['Sectors']:
            names = sectorInfo['Names']
            canonicalName = str(names[0]['Text'])
            sectorX = int(sectorInfo['X'])
            sectorY = int(sectorInfo['Y'])

            encodedFileName = _encodeFileName(rawFileName=canonicalName)

            # When requesting sectors & metadata it's important to do it by position to avoid
            # ambiguity if there are multiple sectors with the same name

            # Download sector file and remove the timestamp that Traveller Map adds
            sectorUrl = f'{_TravellerMapUrl}/api/sec?sx={sectorX}&sy={sectorY}&milieu={milieu}&type=SecondSurvey'
            sectorFilePath = os.path.join(milieuDirPath, encodedFileName + '.sec')
            logging.info(f'Downloading sector file for {canonicalName} in {milieu} from {sectorUrl}')
            sectorData = fileRetriever.downloadToBuffer(url=sectorUrl)

            sectorData = _removeTimestampFromSector(sectorData=_bytesToString(sectorData))
            if sectorData == None:
                raise RuntimeError(f'Failed to remove timestamp from sector file for {canonicalName} in {milieu}')

            logging.info(f'Writing sector file for {canonicalName} in {milieu} to {sectorFilePath}')
            with open(sectorFilePath, 'w', encoding='utf-8') as file:
                file.write(sectorData)

            # Download metadata to memory so name can be updated
            # Parsing the metadata is only strictly required for sectors that have had their name
            # disambiguated, however it's also desirable as an extra check that what is downloaded
            # is basically parsable. The expectation being an exception will be thrown (and the
            # snapshot update will fail) if it's not.
            metadataUrl = f'{_TravellerMapUrl}/api/metadata?sx={sectorX}&sy={sectorY}&milieu={milieu}&accept=text/xml'
            metadataFilePath = os.path.join(milieuDirPath, encodedFileName + '.xml')
            logging.info(f'Downloading metadata for {canonicalName} in {milieu} from {metadataUrl}')
            metadataXml = fileRetriever.downloadToBuffer(url=metadataUrl)
            metadataXml = xml.etree.ElementTree.fromstring(_bytesToString(metadataXml))

            names = metadataXml.findall('./Name')
            if not names:
                raise RuntimeError(f'Failed to find Name elements in sector {canonicalName} in {milieu}')

            mappedName = nameMappings.get(canonicalName)
            if mappedName == None:
                # The name hasn't been mapped so check that the first name matches the canonical name
                # from the universe. If this isn't the case then it could indicate a flaw in my logic
                # elsewhere so barf to fail the snapshot update to give me a chance to fix it
                if names[0].text != canonicalName:
                    raise RuntimeError(f'First name for {canonicalName} in {milieu} doesn\'t match canonical name')
            else:
                if names[0].text != mappedName:
                    # Something is wrong with my logic, barf rather to fail the action
                    raise RuntimeError(f'First name for {canonicalName} in {milieu} doesn\'t match mapped canonical name')

                logging.info(f'Applying disambiguated sector name to metadata for {canonicalName} in {milieu}')
                names[0].text = canonicalName

            logging.info(f'Writing metadata file for {canonicalName} in {milieu} to {metadataFilePath}')
            with open(metadataFilePath, 'w', encoding='utf-8') as file:
                # NOTE: The XML is written to a utf-8 byte array then converted to a string before being written
                # to a utf-8 encoded text file. This is done so line endings are written in native format to
                # avoid problems when I'm testing the script on Windows
                file.write(_bytesToString(xml.etree.ElementTree.tostring(
                    element=metadataXml,
                    encoding='utf-8',
                    xml_declaration=True)))

    finishTime = datetime.datetime.utcnow()
    logging.info(f'Downloaded {fileRetriever.downloadCount()} files in {(finishTime - startTime).total_seconds()} seconds')

    logging.info(f'Sanity checking data')

    # Check for suspiciously few files in a milieu directory
    for milieu in _MilieuList:
        milieuPath = os.path.join(basePath, _MilieuDir, milieu)
        files = [entry for entry in os.listdir(milieuPath) if os.path.isfile(os.path.join(milieuPath, entry))]
        if len(files) < _MinMilieuFiles:
            raise RuntimeError(f'Milieu directory {milieuPath} only contains {len(files)} files')

    # Check for empty files
    for subdir, _, files in os.walk(basePath):
        for file in files:
            filePath = os.path.join(subdir, file)
            fileStat = os.stat(filePath)
            if not fileStat:
                raise RuntimeError(f'Failed to stat {filePath}')
            if fileStat.st_size <= 0:
                raise RuntimeError(f'File {filePath} is empty')

    logging.info(f'Sanity checking completed successfully')

    logging.info(f'Updating timestamp')
    timestampFilePath = os.path.join(basePath, _TimestampFileName)
    with open(timestampFilePath, 'w', encoding='ascii') as file:
        file.write(startTime.strftime(_TimestampFormat))

    logging.info(f'Writing data format')
    dataFormatFilePath = os.path.join(basePath, _DataFormatFileName)
    with open(dataFormatFilePath, 'w', encoding='ascii') as file:
        file.write(_DataFormatVersion)

def main() -> None:
    try:
        logger = logging.getLogger()
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
    except Exception as ex:
        logging.error('Failed to initialise logging', exc_info=ex)
        sys.exit(1)

    try:
        _downloadMapData()
    except Exception as ex:
        logging.error('Failed to download map data', exc_info=ex)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
