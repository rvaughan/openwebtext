from __future__ import print_function

import io
import time
import json
import tarfile
import argparse
import os.path as op
from glob import glob
from hashlib import md5

from multiprocessing import Pool

# for backward compatibility
from six.moves.urllib.request import urlopen

from utils import mkdir, chunks, extract_month
from scrapers import bs4_scraper, newspaper_scraper, raw_scraper

parser = argparse.ArgumentParser()
parser.add_argument("url_file", type=str)
parser.add_argument("--save_uncompressed", action="store_true", default=False)
parser.add_argument("--output_dir", type=str, default="scraped")
parser.add_argument("--n_threads", type=int, default=1)
parser.add_argument("--max_urls", type=int, default=-1)
parser.add_argument("--chunk_size", type=int, default=100)
parser.add_argument("--scraper", type=str, default="newspaper")
parser.add_argument("--compress", action="store_true", default=False)
parser.add_argument("--compress_fmt", type=str, default="xz")
parser.add_argument("--scraper_memoize", action="store_true", default=False)
args = parser.parse_args()


def load_urls(url_file, completed_fids, max_urls=-1):
    with open(url_file) as fh:
        url_entries = [
            (fid, url) for (fid, url) in enumerate(fh) if fid not in completed_fids
        ]
        if max_urls != -1:
            url_entries = url_entries[:max_urls]
    return url_entries


def vet_link(link):
    # check if server responds with non-200 status code or link points to a
    # non-html file
    link_type, link_status = "", -1
    try:
        info = urlopen(link)
        link_type = info.headers["Content-Type"]
        link_status = info.status
    except:
        pass

    # we want "text/html" only!
    is_good_link = False
    if "text/html" in link_type and link_status == 200:
        is_good_link = True

    return is_good_link, link_type


def download(
    url_entry,
    scraper=args.scraper,
    save_uncompressed=args.save_uncompressed,
    memoize=args.scraper_memoize,
):
    uid, url = url_entry
    url = url.strip()
    fid = "{:07d}-{}".format(uid, md5(url.encode()).hexdigest())

    # is_good_link, link_type = vet_link(url)
    # if not is_good_link:
    #     return

    if scraper == "bs4":
        scrape = bs4_scraper
    elif scraper == "newspaper":
        scrape = newspaper_scraper
    elif scraper == "raw":
        scrape = raw_scraper

    text, meta = scrape(url, memoize)
    if text is None or text.strip() == "":
        return ("", "", fid, uid)

    if save_uncompressed:
        month = extract_month(args.url_file)
        data_dir = mkdir(op.join(args.output_dir, "data", month))
        meta_dir = mkdir(op.join(args.output_dir, "meta", month))
        text_fp = op.join(data_dir, "{}.txt".format(fid))
        meta_fp = op.join(meta_dir, "{}.json".format(fid))

        with open(text_fp, "w") as out:
            out.write(text)
        with open(meta_fp, "w") as out:
            json.dump(meta, out)

    return (text, meta, fid, uid)


def archive_chunk(month, cid, cdata, out_dir, fmt):
    if fmt not in ["xz", "bz2", "gz"]:
        raise Exception('Compression format must be "xz", "bz2", or "gz"')

    mkdir(out_dir)
    texts, metas, fids, uids = zip(*cdata)
    data_tar = op.join(out_dir, "{}-{}_data.{}".format(month, cid, fmt))
    meta_tar = op.join(out_dir, "{}-{}_meta.{}".format(month, cid, fmt))
    tar_fps, texts, exts = [data_tar, meta_tar], [texts, metas], ["txt", "json"]

    for tar_fp, txts, ext in zip(tar_fps, texts, exts):
        with tarfile.open(tar_fp, "w:" + fmt) as tar:
            for f, fid in zip(txts, fids):
                if f == "":
                    continue

                if ext == "json":
                    f = json.dumps(f)

                f = f.encode("utf-8")
                t = tarfile.TarInfo("{}.{}".format(fid, ext))
                t.size = len(f)
                tar.addfile(t, io.BytesIO(f))

    return data_tar, meta_tar


#######################################################################
#                           Util functions                            #
#######################################################################


def get_state(month, out_dir):
    mkdir("state")
    latest_cid = 0
    completed_uids = set()
    state_fp = op.join("state", "{}.txt".format(month))
    if op.isfile(state_fp):
        archives = glob(op.join(out_dir, "{}-*".format(month)))
        latest_cid = max([int(a.split("-")[-1].split("_")[0]) for a in archives])
        with open(state_fp, "r") as fh:
            completed_uids = set(int(i.strip()) for i in list(fh))
    return completed_uids, state_fp, latest_cid


def set_state(state_fp, cdata):
    _, _, _, uids = zip(*cdata)
    with open(state_fp, "a+") as handle:
        for uid in uids:
            handle.write("{}\n".format(uid))


if __name__ == "__main__":
    month = extract_month(args.url_file)
    completed_uids, state_fp, prev_cid = get_state(month, args.output_dir)
    url_entries = load_urls(args.url_file, completed_uids, args.max_urls)

    p = Pool(args.n_threads)
    for i, chunk in enumerate(chunks(url_entries, args.chunk_size)):
        cid = prev_cid + i + 1
        print("Downloading chunk {}".format(cid))
        t1 = time.time()
        cdata = list(p.imap(download, chunk))
        set_state(state_fp, cdata)
        print("Chunk time: {} seconds".format(time.time() - t1))

        if args.compress:
            print("Compressing...")
            t2 = time.time()
            archive_chunk(month, cid, cdata, args.output_dir, args.compress_fmt)
            print("Tarballs created in {} seconds\n".format(time.time() - t2))

    print("Done!")
