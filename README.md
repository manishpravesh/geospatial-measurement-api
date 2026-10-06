# Geospatial File Measurement API

Upload a zipped shapefile, a KML file, or a KMZ archive. The service reads each feature and returns its geometry, attributes, and a measurement in metres.

Polygons get an area. Lines get a length. Points are returned as they are, with no measurement. If a geometry cannot be measured, that feature is reported with a note and the rest of the file still goes through.

Geographic coordinates are projected before anything is calculated. A file in EPSG:4326 does not get an area in square degrees.

## Setup

Python 3.11 or 3.12. CI runs the suite on 3.12. You do not need a separate GDAL install: the shapefile reader uses GeoPandas, and the wheels pull in their own GDAL.

From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

On macOS or Linux the activation line is `source .venv/bin/activate`. Everything after that is the same.

The app listens on http://127.0.0.1:8000. Interactive docs are at http://127.0.0.1:8000/docs. The database and uploaded files live in `data/`, which is created on startup and is not committed.

Optional settings go in a `.env` file. See `.env.example`.

```powershell
pip install -r requirements-dev.txt
pytest
```

`requirements.txt` is the runtime set. `requirements-dev.txt` adds pytest, httpx, and ruff.

Docker, from the project root:

```powershell
docker compose up --build
```

The container listens on port 8000 and keeps the sqlite database in a volume named `api-data`.

## API

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/api/files/` | Upload a `.kml`, `.kmz`, or `.zip` shapefile and measure it |
| `GET` | `/api/files/` | List the 500 most recent uploads |
| `GET` | `/api/files/{id}/` | File summary |
| `GET` | `/api/files/{id}/measurements/` | Per-feature measurements |
| `DELETE` | `/api/files/{id}/` | Delete the file and its measurements |
| `GET` | `/health` | Database check |

The same paths work without the trailing slash. In PowerShell, `curl` is an alias for `Invoke-WebRequest`, so the examples use `curl.exe`.

### Upload

`POST /api/files/` takes `multipart/form-data`.

| Field | Required | Meaning |
| --- | --- | --- |
| `file` | yes | `.kml`, `.kmz`, or a `.zip` with one shapefile (`.shp`, `.shx`, and `.dbf`) |
| `assume_crs` | no | CRS to use when the file has none, for example `EPSG:4326`. Ignored when the file already has a CRS |

A stored file returns **201**. Read `status` in the body.

- `COMPLETED` means the features were read. Individual features can still be unmeasured; their notes say why.
- `FAILED` means the file was kept but could not be read (broken XML, a shapefile GeoPandas cannot open, more features than the limit). `error` says why. `GET` on the same id returns the same record.

Nothing is stored, and the response is **400**, when the extension is wrong, the zip is not a zip, the archive tries to escape its folder, a shapefile is missing `.shx` or `.dbf`, or the zip contains more than one `.shp`. A file over the size limit returns **413**. An unknown `assume_crs` returns **400**.

```powershell
curl.exe -F "file=@samples/square_1km.kml" http://127.0.0.1:8000/api/files/
```

`samples/square_1km.kml` is a 1 km square built in UTM zone 43N (EPSG:32643) and written out as WGS 84. In that projected CRS the square is exactly 1,000,000 m². The KML writes each coordinate to 8 decimal places, and the API returns `1000000.512` after projecting it back. `samples/square_1km.zip` is the same square as a shapefile, with the coordinates stored at full precision, and comes back as `1000000.0`. `samples/track_2500m.kml` is a 2.5 km line plus a point.

```powershell
curl.exe -F "file=@samples/square_1km.zip" http://127.0.0.1:8000/api/files/
```

A shapefile with no `.prj` is accepted and stored, but its measurements stay empty until you send the CRS yourself:

```powershell
curl.exe -F "file=@parcels.zip" -F "assume_crs=EPSG:32643" http://127.0.0.1:8000/api/files/
```

A successful upload looks like this. `id` and `created_at` will differ, and `byte_size` follows the file you sent.

```json
{
  "id": "8f3c1d0a2b7e4c5d9a1f6e0b4c8d2a77",
  "filename": "square_1km.kml",
  "feature_count": 1,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "byte_size": 610,
  "layer": "Square kilometre",
  "error": null,
  "notes": [
    "KML is WGS 84. The file CRS is EPSG:4326, including when the document does not say so.",
    "Area and length are calculated in a projected CRS chosen per feature. Longitude and latitude are not treated as metres."
  ],
  "created_at": "2026-10-07T00:00:00+00:00"
}
```

The fields from the brief are `id`, `filename`, `feature_count`, `crs`, and `status`. The others are there because they are useful once a file can fail, or once you want to know which layer was read.

### File information

```powershell
curl.exe http://127.0.0.1:8000/api/files/8f3c1d0a2b7e4c5d9a1f6e0b4c8d2a77/
```

Same body as the upload response. An unknown id is **404**. Ids are 32 hex characters; anything else is also **404**.

### Measurements

```powershell
curl.exe "http://127.0.0.1:8000/api/files/8f3c1d0a2b7e4c5d9a1f6e0b4c8d2a77/measurements/"
```

Query parameters:

| Param | Default | Meaning |
| --- | --- | --- |
| `offset` | `0` | Skip this many matching features |
| `limit` | `1000` | Page size, maximum 5000 |
| `geometry_type` | none | Case-insensitive filter, for example `Polygon` or `Point` |
| `include_geometry` | `true` | Set `false` to drop coordinates from the payload |

`feature_count` is the whole file. `matched_features` and `summary` follow the filter. The summary is in metres: area from polygons, length from lines. A feature whose CRS could not be used counts as unmeasured and does not add a fake zero.

Values are planar measurements in the CRS named by `calculated_in`, rounded to 3 decimal places (millimetres, or 0.001 m²). They are not geodesic. On a local survey the difference is small. On a polygon the size of a country it is not, which is why those geometries are called out in `note`.

```json
{
  "file_id": "8f3c1d0a2b7e4c5d9a1f6e0b4c8d2a77",
  "filename": "square_1km.kml",
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "feature_count": 1,
  "matched_features": 1,
  "offset": 0,
  "limit": 1000,
  "error": null,
  "notes": [
    "KML is WGS 84. The file CRS is EPSG:4326, including when the document does not say so.",
    "Area and length are calculated in a projected CRS chosen per feature. Longitude and latitude are not treated as metres."
  ],
  "summary": {
    "total_area_square_meters": 1000000.512,
    "total_length_meters": 0.0,
    "measured_features": 1,
    "unmeasured_features": 0
  },
  "features": [
    {
      "feature_id": "0",
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [75.00920935, 12.66418772],
            [75.00920967, 12.67323067],
            [75.0, 12.67323083],
            [75.0, 12.66418788],
            [75.00920935, 12.66418772]
          ]
        ]
      },
      "crs": "EPSG:4326",
      "properties": {
        "name": "North plot",
        "description": "1 km square built in UTM zone 43N, written here as WGS 84."
      },
      "measurement": {
        "type": "area",
        "value": 1000000.512,
        "unit": "square_meters",
        "calculated_in": "EPSG:32643",
        "area_square_meters": 1000000.512,
        "length_meters": null,
        "note": null
      }
    }
  ]
}
```

The `geometry.coordinates` ring above is the sample square in longitude and latitude. `feature_id` is the zero-based index of the feature in file order. Attributes from the shapefile, or from the KML placemark (`name`, `description`, `folder`, extended data), are in `properties`.

`measurement.type` is:

| Type | Geometries | `value` |
| --- | --- | --- |
| `area` | `Polygon`, `MultiPolygon` | square metres |
| `length` | `LineString`, `MultiLineString` | metres |
| `none` | `Point`, `MultiPoint`, empty, unsupported, or skipped | `null` |
| `mixed` | a collection that has both area and length | `null`, with both `area_square_meters` and `length_meters` set |

`calculated_in` is the CRS used for the maths. For a local file in EPSG:4326 that is usually a UTM zone, not `EPSG:4326`.

Multi-part polygons and lines are normal shapefile types, so they are measured. A geometry we do not understand, or one that fails while being repaired, does not fail the request. That feature comes back as `none` with a note.

### Limits

Defaults, overridable from the environment:

- upload size: 50 MB
- uncompressed archive: 200 MB
- files inside one archive: 2000
- features in one file: 20,000

## Architecture

### Application structure

```
app/
  main.py                 FastAPI app, health check
  config.py               settings and environment variables
  database.py             sqlite engine and sessions
  models.py               uploaded file and feature tables
  schemas.py              response bodies
  api/files.py            HTTP routes
  services/archive.py     zip checks, shapefile and KMZ unpacking
  services/kml.py         KML placemark reader
  services/shapefile.py   shapefile reader (GeoPandas)
  services/crs.py         which projected CRS to measure in
  services/measure.py     area and length
  services/ingest.py      the upload pipeline
tests/                    projection maths, readers, and the HTTP API
samples/                  a 1 km square and a 2.5 km track
```

State sits in sqlite (`data/app.db`). The original upload sits on disk under `data/uploads/{id}/`. Measurements are rows, not a second parse of the file, so `GET` does not depend on GeoPandas still being able to open the archive.

### File-processing flow

1. The extension has to be `.kml`, `.kmz`, or `.zip`. The filename is taken from the base name only.
2. The body is written to disk with a size cap. An empty body is rejected.
3. A zip is opened as data, not extracted with the archive's own paths. Member names are checked for `..`, absolute paths, and drive letters before any byte is written. Only shapefile sidecars are unpacked from a `.zip` (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`, and a few index files). A `.kmz` only contributes its KML, preferring `doc.kml`. `__MACOSX` junk is ignored.
4. A shapefile zip must contain exactly one `.shp`, plus the matching `.shx` and `.dbf`. More than one layer is an error, rather than a silent pick. A missing `.prj` is allowed and means "CRS unknown".
5. KML is parsed as XML with defusedxml. Placemarks become features. Folders, names, descriptions, and extended data become properties. Coordinates are longitude, latitude, and an optional altitude. The file CRS is EPSG:4326, which is what the KML specification says, including when the document never mentions a CRS.
6. Each feature is measured on its own. One bad geometry does not drop the others.
7. The file row and the feature rows are committed. `status` is `COMPLETED` or `FAILED`.

Processing happens inside the request. For the files this service is aimed at, that keeps the client simple: the 201 response is already measured. A 50 MB cap and a 20,000 feature cap are there so one request cannot run away with the process.

### Measurement calculation flow

For each feature:

1. No geometry, or an empty one, stops here. `type` is `none`.
2. Altitude is remembered for the note, then dropped. The number is a horizontal distance or area, not a 3D one.
3. An invalid geometry is repaired with `make_valid`. If that still produces nothing usable, the feature is skipped with a note.
4. Points and multipoints stop here. There is nothing to measure.
5. Anything that is not a polygon, a line, or a collection of those is skipped with a note. The process does not raise.
6. If the file has no CRS, area and length stay null. Guessing WGS84 for a shapefile with no `.prj` would produce a confident wrong number.
7. Otherwise the geometry is projected (see below) and Shapely's planar area or length is scaled into metres.

Holes in a polygon count. A multipolygon's area is the sum of its parts. A collection that contains both a polygon and a line reports both numbers and `type: "mixed"`.

### CRS handling

Degrees are not metres, and a degree of longitude shrinks as you leave the equator. The service never multiplies latitude and longitude together and calls it an area.

`app/services/crs.py` picks the CRS:

1. If the file CRS is already projected, measure in that CRS and convert the axis unit to metres. A foot-based state plane file stays in that CRS; the result is still reported in metres. The file's author chose the projection. Replacing it with UTM would hide that.
2. If the file CRS is geographic, look at the feature in WGS 84 and choose a projection for that feature:
   - Latitude above 84°N or below 80°S: Universal Polar Stereographic. UTM is not defined there.
   - Bounding box wider than 6° of longitude or 12° of latitude: a local azimuthal projection centred on the feature. Equal-area (`laea`) for polygons, equidistant (`aeqd`) for lines. One UTM zone would distort the far side of a geometry that size.
   - Otherwise: the UTM zone of a point on the feature. Zone numbers follow the standard 6° bands. The Norway and Svalbard exceptions are not applied.
3. The geometry is transformed into that CRS, then measured.

`crs` on the file and on the feature is the source CRS (`EPSG:4326` for KML). `measurement.calculated_in` is the CRS the number was actually computed in (`EPSG:32643` for the sample square).

A shapefile with no `.prj` stays unmeasured unless the upload includes `assume_crs`. If the file already has a CRS, `assume_crs` is ignored and the response says so.

## Design decisions

**FastAPI rather than Django REST framework.** The surface is a handful of endpoints and a pipeline. FastAPI gets request validation, OpenAPI, and a short path from the upload to JSON without an admin site or a template layer. Django would be the better choice if this had to live inside an existing Django project, or if the next requirement was a permission model and an admin for the uploads.

**sqlite rather than PostGIS.** The job is "read this file, store the measurements, give them back." It is not "find every parcel that intersects this window." sqlite runs with no extra service, which matches a local setup and the Docker file. PostGIS would earn its place if we started querying by geometry.

**Synchronous processing.** The response can say `COMPLETED` immediately, which matches the way the file-info example is written. A queue (RQ, Celery, a background task) is the right follow-up once files are large enough that the request would time out. The status field is already there for that.

**A small KML reader rather than GDAL's KML driver.** KML from the tools people actually use is placemarks, folders, extended data, tracks, and the occasional altitude. Parsing that directly is easier to test, and it does not depend on which drivers a particular GDAL wheel was built with. Shapefiles are the opposite: the format is old and fiddly (code pages, `.prj`, the sidecar files), and GeoPandas already does that work. KMZ is just a zip around the same KML.

**defusedxml.** The upload is someone else's XML. The parser refuses dangerous entity declarations instead of resolving them.

**Do not guess a missing shapefile CRS.** KML is specified as WGS 84, so assuming EPSG:4326 is following the format. A shapefile without `.prj` could be anything. `assume_crs` is an explicit opt-in for that case.

**One shapefile per zip.** A zip of a whole project folder often has several layers, with different schemas. Failing with the list of names is clearer than concatenating them and hoping the properties line up.

**UTM for local geographic data, and a different projection when UTM is a bad fit.** `GeoSeries.estimate_utm_crs()` would have covered the common case in one call. It does not say what happens at the poles or for a geometry wider than a zone, and those are the cases where a silent UTM zone is most wrong. The rules are in `measurement_crs` and covered by tests.

**Geodesic area was the other option.** `pyproj.Geod` can compute ellipsoid area without picking a projection, and the tests use it as an independent check on the 1 km square. The brief asks for a projected CRS, so that is what the API returns. Geodesic would be a good second number later, not a replacement, because a surveyor often wants the area in the same CRS the drawing was made in.

**Planar, horizontal measurements.** Altitude is kept on the geometry you get back, and ignored for the number. A 5 km climb over a 1 km line is not a 5 km parcel edge.

**201 even when `status` is `FAILED`.** The resource exists and can be fetched. Clients that only look at the HTTP code will miss it, which is why `status` is on the body and in the docs. Rejecting the request entirely would also have been reasonable. I kept the record so a bad file is visible in the list instead of disappearing into a 422 with no id.

## Learning

The bug I most wanted to avoid is an area in square degrees. It looks like a normal float, it is deterministic, and it is meaningless. A degree of longitude at the equator is about 111 km. The same degree at 60° north is about half that. Two polygons with the same bounding box in degrees do not have the same area, and the tests pin that down: a 1° square at the equator has to come out larger than the same square at 60°N, and the 1 km sample has to come back near 1,000,000 m² after a trip through WGS 84.

The zip side taught a smaller lesson. Reading a shapefile is the easy part. Refusing `../` in a member name, insisting on `.shx` and `.dbf`, and not inventing a `.prj` is what keeps the endpoint from either crashing or lying.

KML looked like something to hand to GDAL and forget. The files I cared about were small XML documents with a lot of optional pieces (folders, `ExtendedData`, a track instead of a line, an altitude on every coordinate). A reader that only understands those pieces was easier to trust than a driver I could not see fail.

## Future scope

- A job queue, so a big extract does not sit inside the HTTP request. `PROCESSING` is already a status the API understands.
- GeoJSON and GeoPackage. Both are what people export now. Shapefile and KML are what the brief asked for.
- A geodesic area next to the projected one, for polygons that do not fit a single zone.
- Let the client ask for hectares or kilometres instead of only metres.
- Auth, if more than one person is uploading into the same database.
- PostGIS, if the next question is spatial ("which features intersect this polygon?") rather than "how big is each feature?"
