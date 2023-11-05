#!/usr/bin/env python3

import datetime
import downloader
import itertools
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import typing
import urllib.error
import urllib.parse
import urllib.request

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
_DownloadDelaySeconds = 0
_MilieuList = ['IW', 'M0', 'M990', 'M1105', 'M1120', 'M1201', 'M1248', 'M1900']
_MinMilieuFiles = 3 # Must have at least universe file and .sec and metadata files for 1 sector
_SectorTimestampPattern = re.compile('^#\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-\d{2}:\d{2}\s*$')
_DataFormatVersion = 3

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
    return bytes.decode('utf-8')

def _encodeFileName(rawFileName: str) -> str:
    escapedFileName = ''
    for index in range(0, len(rawFileName)):
        char = rawFileName[index]
        if char in _EncodedCharacters:
            char = f'%{format(ord(char), "x")}'
        escapedFileName += char
    return escapedFileName

def _parseUniverseData(universeData: bytes) -> typing.List[str]:
    universeJson = json.loads(_bytesToString(universeData))
    if 'Sectors' not in universeJson:
        raise RuntimeError('Invalid sector list')

    sectors = []
    for sectorInfo in universeJson['Sectors']:
        names = sectorInfo['Names']
        canonicalName = names[0]['Text']
        sectors.append(canonicalName)

    return sectors

# Remove timestamps from sector files downloaded from Traveller Map. This is needed as otherwise
# every sector file will be seen as modified every time the update is performed
def _removeTimestampFromSector(sectorFilePath: str) -> int:
    encoding = 'utf-8'
    linesRemoved = 0
    with open(sectorFilePath, encoding=encoding) as inputFile:
        with tempfile.NamedTemporaryFile(
                mode='w',
                encoding=encoding,
                dir=os.path.dirname(sectorFilePath),
                delete=False) as outFile:
            for line in inputFile:
                if not _SectorTimestampPattern.search(line):
                    print(line, end='', file=outFile)
                else:
                    linesRemoved += 1
    os.replace(outFile.name, inputFile.name)
    return linesRemoved

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

    downloadQueue.append((
        f'{_TravellerMapUrl}/t5ss/sophonts',
        os.path.join(basePath, _SophontsFileName),
        None))

    downloadQueue.append((
        f'{_TravellerMapUrl}/t5ss/allegiances',
        os.path.join(basePath, _AllegiancesFileName),
        None))
    
    downloadQueue.append((
        f'{_TravellerMapUrl}/res/mains.json',
        os.path.join(basePath, _MainsFileName),
        None))    

    for milieu in _MilieuList:
        downloadQueue.append((
            f'{_TravellerMapUrl}/api/universe?milieu={urllib.parse.quote(milieu)}&requireData=1',
            os.path.join(basePath, _MilieuDir, milieu, _UniverseFileName),
            milieu))

    logging.info(f'Downloading new map data')
    startTime = datetime.datetime.utcnow()
    downloadCount = 0
    while downloadQueue:
        downloadInfo = downloadQueue.pop(0)
        downloadCount += 1

        url = downloadInfo[0]
        filePath = downloadInfo[1]
        milieu = downloadInfo[2]

        dirPath = os.path.dirname(filePath)
        if not os.path.exists(dirPath):
            os.makedirs(dirPath)

        fileRetriever.downloadToFile(url=url, filePath=filePath)

        if milieu:
            # If there was a milieu specified it means this is a universe file that was downloaded
            # so we want to add the sectors
            with open(filePath, 'rb') as file:
                fileContent = file.read()
            sectors = _parseUniverseData(universeData=fileContent)
            for sector in sectors:
                quotedSector = urllib.parse.quote(sector)
                quotedMilieu = urllib.parse.quote(milieu)

                sectorFileName = _encodeFileName(rawFileName=sector) + '.sec'
                downloadQueue.append((
                    f'{_TravellerMapUrl}/api/sec?sector={quotedSector}&milieu={quotedMilieu}&type=SecondSurvey',
                    os.path.join(dirPath, sectorFileName),
                    None))

                metadataFileName = _encodeFileName(rawFileName=sector) + '.json'
                downloadQueue.append((
                    f'{_TravellerMapUrl}/api/metadata?sector={quotedSector}&milieu={quotedMilieu}',
                    os.path.join(dirPath, metadataFileName),
                    None))

        if downloadQueue:
            # Delay before downloading the next file
            time.sleep(_DownloadDelaySeconds)

    finishTime = datetime.datetime.utcnow()
    logging.info(f'Downloaded {downloadCount} files in {(finishTime - startTime).total_seconds()} seconds')

    logging.info(f'Removing timestamps from sector files')
    for milieu in _MilieuList:
        milieuPath = os.path.join(basePath, _MilieuDir, milieu)
        if not os.path.isdir(milieuPath):
            raise RuntimeError(f'Milieu directory {milieuPath} doesn\'t exist')

        sectorFilePathList = pathlib.Path(milieuPath).rglob('*.sec')
        for sectorFilePath in sectorFilePathList:
            logging.info(f'Removing timestamp from {sectorFilePath}')
            removedLines = _removeTimestampFromSector(sectorFilePath=sectorFilePath)
            if removedLines <= 0:
                raise RuntimeError(f'Failed to find timestamp in sector file {sectorFilePath}')
            if removedLines > 1:
                raise RuntimeError(f'Found more than one timestamp in sector file {sectorFilePath}')

    logging.info(f'Sanity checking data')
    for milieu in _MilieuList:
        milieuPath = os.path.join(basePath, _MilieuDir, milieu)
        files = [entry for entry in os.listdir(milieuPath) if os.path.isfile(os.path.join(milieuPath, entry))]
        if len(files) < _MinMilieuFiles:
            raise RuntimeError(f'Milieu directory {milieuPath} only contains {len(files)} files')

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
    with open(os.path.join(basePath, _TimestampFileName), 'wb') as file:
        file.write(str(startTime.strftime(_TimestampFormat)).encode('ascii'))

    logging.info(f'Writing data format')
    with open(os.path.join(basePath, _DataFormatFileName), 'wb') as file:
        file.write(str(_DataFormatVersion).encode('ascii'))

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
