# KYC integrity

[![CI](https://github.com/talalz94/kyc-integrity/actions/workflows/ci.yml/badge.svg)](https://github.com/talalz94/kyc-integrity/actions/workflows/ci.yml)

Identity document forgery and face liveness detection, judged the way biometric systems are judged: error rates at fixed operating points, not accuracy.

**Why this repo exists.** I led and built a KYC computer vision suite in production: document checks, face matching and liveness. That work is private, so this is a public rebuild of the same problems on public data, written from scratch. No employer or client code, data or details are in here.

**Status.** Started. The FantasyID download (with an MD5 check against Zenodo) and safe unpacking are in place and tested. Training needs a GPU, so models come next, on a rented one. See the plan below.

## Plan

1. **Forgery detection on [FantasyID](https://zenodo.org/records/17063366)** (CC BY 4.0): 13 fantasy ID card designs in ten languages, printed on plastic cards and captured with two phones and an office scanner, plus forged versions made by digitally editing those captures (swapped faces, altered text). Reported as the false negative rate at a 10% false positive rate, the operating point where the dataset's authors found published detectors miss about half of forged IDs. The paper sets that threshold on its validation split, FantasyID-val ([Zenodo 17063494](https://zenodo.org/records/17063494)). That split is restricted: access is by request under a noncommercial research agreement, and `kyc download` does not fetch it. Until access is granted, the threshold is set on a validation split carved from the open training data, and the results will say so. The test set is used unchanged and only for the final numbers, so it can be compared with the paper. If access to FantasyID-val is granted, results on the paper's own protocol will go in a separate row.
2. **Face spoof detection on CelebA-Spoof** (noncommercial research only): BPCER at an APCER of 5% and 1% (BPCER20 and BPCER100), with each threshold fixed on the validation split, and APCER at those thresholds per spoof type and per lighting condition. EER and ACER appear only for comparison with the CelebA-Spoof paper, which sets its threshold at the EER of the test set. No trained weights will be published, because the dataset agreement covers derived data.
3. **Serving:** ONNX export, quantisation, and a CPU vs GPU latency table, with the change in those error rates after quantisation.
4. **Model card** that states intended use (decision support for a trained reviewer, never the only decision), out of scope uses and license limits.

## Quickstart

```bash
uv run kyc download   # about 2.6 GB from Zenodo, after you accept the notice
uv run kyc extract    # checks the MD5 again, then unpacks into data/fantasyid
```

`kyc download` saves the archive as `data/raw/FantasyID.tgz` and checks it against the MD5 that Zenodo publishes. On a terminal it shows how much has arrived so far. If the download stops partway, run the same command again: it asks Zenodo for only the bytes still missing. If the saved file is already complete and passes the MD5 check, nothing more is fetched. If the server sends the whole file instead, or cannot send the rest, the file starts over. A download that fails the MD5 check is deleted, so the next run starts clean.

`kyc extract` checks the MD5 again before it unpacks anything. It unpacks with Python's "data" filter, which keeps every file inside the target folder and refuses links that point outside it and device files. Python 3.12.11 and 3.13.4 fixed known ways around that filter, so this repo pins Python 3.12.13 (uv installs it for you) and `kyc extract` refuses to run on an older release.

To keep the data somewhere else, give both commands the same folder: `uv run kyc --data-dir <folder> download`, then `uv run kyc --data-dir <folder> extract`. Both commands put a `.gitignore` in the folders they fill (`raw` and `fantasyid`), so the data stays out of git wherever you keep it.

## Credits and license

FantasyID by Korshunov et al., Idiap Research Institute, IJCB 2025. The open Zenodo record this repo downloads (17063366) is CC BY 4.0; the FantasyID-val part (17063494) is restricted to noncommercial research, and this repo does not download it. CelebA-Spoof is for noncommercial research only. Neither dataset is included in this repo. The code in this repo is MIT licensed; that license does not cover any dataset.
