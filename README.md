# Auto-Jimmy Data
This repo contains the universe data used by [Auto-Jimmy](https://github.com/cthulhustig/autojimmy).
The data is a snapshot taken from Traveller Map using its [web API](https://travellermap.com/doc/api). It's automatically updated
once a week by a GitHub action.

## FAQ
### I use Auto-Jimmy, do I need to do something with this repo?
No, this repo is used automatically by Auto-Jimmy. If you want to update your snapshot of the
universe data, just click the Download Universe Data button.

### Why is this needed?
Auto-Jimmy requires a local snapshot of the Traveller Map universe data for performance reasons.
As the Traveller Map universe data is being updated over time, this means the snapshot used by
Auto-Jimmy can become out of date. To avoid this, Auto-Jimmy has the ability to download an
updated copy of the data.
In early releases of Auto-Jimmy, when an update was performed, the application would download the
data directly from Traveller Map using its web API. Unfortunately, as Traveller Map doesn't have
an API to download the entire universe in one go, this meant downloading each sector file
individually. The problem with this approach is there are a LOT of sector files, so it meant making
100s of requests against Traveller Map. The result of this was it took a long time to update and
put extra load on Traveller Map.
One possibility would be to pull the required data directly from the [Traveller Map repo](https://github.com/inexorabletash/travellermap). However, this is problematic as the source data in the repo is not in a consistent
format and it would mean the update mechanism may fail if the structure of the Traveller Map
repo changed in the future.
The solution I've gone with is to create a snapshot using the web API. This is a stable API and
it present the data in a consistent format.

## Legal
This repo contains a snapshot of the Traveller Map web interface and universe data. The copy of
the web interface is used to prevent issues caused by the continued development of Traveller Map.
The universe data is used to increase performance and allow the application to work offline. These
files are the copyright of their creators.

Auto-Jimmy is not endorsed by the wonderful people at Traveller Map, Mongoose Publishing or Far
Future Enterprises.
