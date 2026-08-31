#!/usr/bin/env python3
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


spec = importlib.util.spec_from_file_location("prepare", Path("scripts/prepare.py"))
prepare = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prepare
spec.loader.exec_module(prepare)


class Response(io.BytesIO):
    def __init__(self, data, status, headers):
        super().__init__(data)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class PrepareTest(unittest.TestCase):
    def test_resume_dedupe_collision_redownload_and_layers(self):
        blobs = {"https://example.test/a/same.bin": b"abcdef", "https://example.test/b/same.bin": b"ghijkl"}
        ranges = []

        def urlopen(request, timeout):
            data = blobs[request.full_url]
            header = request.get_header("Range")
            start = int(header.split("=")[1].split("-")[0]) if header else 0
            if header:
                ranges.append(start)
            headers = {"Content-Length": str(len(data) - start)}
            if header:
                headers["Content-Range"] = f"bytes {start}-{len(data)-1}/{len(data)}"
            return Response(data[start:], 206 if header else 200, headers)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            first_url = next(iter(blobs))
            (cache / f"{prepare.url_key(first_url)}.part").write_bytes(b"abc")
            entries, duplicates = prepare.parse_manifest(
                "\n".join([first_url, first_url, list(blobs)[1]]), False
            )
            files = {}
            with mock.patch.object(prepare.URL_OPENER, "open", urlopen):
                for entry in entries:
                    files[entry.url] = prepare.download_blob(entry.url, cache, 1, 10)
            layers = prepare.pack_layers(entries, files, 17_000)
            context = root / "context"
            prepare.materialize(context, layers, files)
            files_out = [path for path in context.glob("layer-*/data/*") if path.is_file()]
            self.assertEqual(duplicates, 1)
            self.assertEqual(len(files_out), 2)
            self.assertEqual({path.read_bytes() for path in files_out}, set(blobs.values()))
            self.assertEqual(len({path.name for path in files_out}), 2)
            self.assertIn(3, ranges)
            self.assertEqual(
                (context / "Dockerfile").read_text(),
                "FROM scratch\nCOPY layer-0000/ /\nCOPY layer-0001/ /\n",
            )
            blobs[first_url] = b"updated"
            with mock.patch.object(prepare.URL_OPENER, "open", urlopen):
                refreshed = prepare.download_blob(first_url, cache, 1, 10)
            self.assertEqual(refreshed.read_bytes(), b"updated")


if __name__ == "__main__":
    unittest.main()
