#!/usr/bin/env python3
"""Count how many times a string occurs in a saved image, layer by layer.

    docker image save xbloom-hub:local | python3 tools/count-in-image.py PATTERN_FILE

PATTERN_FILE holds the string (surrounding whitespace ignored), so a secret
is never on a command line; only the count is printed. Layers may be plain
or gzip-compressed tar (newer Docker saves them compressed, where a plain
grep over the archive finds nothing). Run it once with a string the image
does contain: a 0 means something only if that count is above 0.
"""
import gzip
import sys
import tarfile


def blobs(stream):
    with tarfile.open(fileobj=stream, mode="r|") as outer:
        for member in outer:
            if member.isfile():
                data = outer.extractfile(member).read()
                yield gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def main() -> None:
    pattern = open(sys.argv[1], "rb").read().strip()
    if not pattern:
        sys.exit("the pattern file is empty")
    print(sum(blob.count(pattern) for blob in blobs(sys.stdin.buffer)))


if __name__ == "__main__":
    main()
