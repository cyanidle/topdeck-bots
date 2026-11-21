#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "analyzer.h"
#include "hashindex.h"
#include "ocrbench.h"
#include "overlay.h"
#include "phash.h"
#include "report.h"

namespace {

const char *kUsage =
    "cardscan - MTG card detector (OpenCV + Tesseract)\n"
    "\n"
    "Usage: cardscan <input.(jpg|png|mp4|mov|...)> [options]\n"
    "\n"
    "Options:\n"
    "  --json <file>         write detection report as JSON\n"
    "  --overlay <file>      write debug overlay (image -> png/jpg, video -> mp4)\n"
    "  --set-codes <file>    known set codes (whitespace/comma separated) for snapping OCR\n"
    "  --debug-dir <dir>     dump annotated frames and per-track card crops\n"
    "  --debug-every <n>     annotated frame cadence in the debug dir (default 30)\n"
    "  --min-hits <n>        frames a track must survive to be counted (default 4)\n"
    "  --stride <n>          process every n-th frame of a video (default 1)\n"
    "  --max-frames <n>      stop after n frames (video)\n"
    "  --no-ocr              skip text recognition (geometry only)\n"
    "  --no-title            disable card-name fallback\n"
    "  --reads <file>        rebuild the card report from a reads.txt timeline (no OCR)\n"
    "  --parse <text>        print how the collector line parser reads a string\n"
    "  --build-index <meta.jsonl> <out.tsv>   hash a Scryfall image cache into a lookup index\n"
    "  --lookup <index.tsv> <image>           rank index cards for a card image\n"
    "  --phash-index <index.tsv>              identify cards by artwork hash (Scryfall index)\n"
    "  --ocr-bench <meta.jsonl> [--samples n]  rank OCR variants on synthetic collector lines\n"
    "  --probe-card <img>    run the collector-line locator + OCR on one card crop and print the read\n"
    "  --compare <a> <b>     print perceptual-hash / colour-signature distance of two images\n"
    "  --verbose             per-track logging to stderr\n"
    "  --help                this message\n";

void writeOverlayImage(const std::string &path, const std::string &output,
                       const cardscan::ImageResult &result) {
  cv::Mat image = cv::imread(path, cv::IMREAD_COLOR);
  if (image.empty()) return;
  cardscan::OverlayStyle style;
  for (const auto &c : result.cards) {
    cardscan::Quad quad;
    if (c.quad.size() == 4) {
      std::copy(c.quad.begin(), c.quad.end(), quad.begin());
    }
    std::string label;
    if (!c.setCode.empty()) label += c.setCode + " ";
    if (!c.number.empty()) {
      label += c.number;
      if (!c.total.empty()) label += "/" + c.total;
    } else if (!c.name.empty()) {
      label += c.name;
    } else {
      label += "?";
    }
    char buf[64];
    std::snprintf(buf, sizeof(buf), "  conf=%.2f", c.confidence);
    label += buf;
    const cv::Scalar color = c.identified ? style.identified : style.unidentified;
    cardscan::drawQuad(image, quad, color, label, style);
  }
  char hud[128];
  std::snprintf(hud, sizeof(hud), "%zu card(s), %d identified", result.cards.size(),
                static_cast<int>(std::count_if(result.cards.begin(), result.cards.end(),
                                               [](const cardscan::CardResult &c) { return c.identified; })));
  cardscan::drawHud(image, {hud}, style);
  cv::imwrite(output, image);
}

int run(int argc, char **argv) {
  std::string probeCard;
  std::string compareA;
  std::string compareB;
  if (argc < 2) {
    std::cerr << kUsage;
    return 2;
  }
  std::string input;
  std::string jsonPath;
  std::string overlayPath;
  std::string setCodesPath;
  std::string readsPath;
  std::string parseText;
  std::string indexMeta;
  std::string indexOut;
  std::string lookupIndex;
  std::string lookupImage;
  std::string benchMeta;
  int benchSamples = 100;
  cardscan::AnalyzerOptions options;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto next = [&](const char *name) -> std::string {
      if (i + 1 >= argc) throw std::runtime_error(std::string("missing value for ") + name);
      return argv[++i];
    };
    try {
      if (arg == "--help" || arg == "-h") {
        std::cout << kUsage;
        return 0;
      } else if (arg == "--json") {
        jsonPath = next("--json");
      } else if (arg == "--overlay") {
        overlayPath = next("--overlay");
      } else if (arg == "--debug-dir") {
        options.debugDir = next("--debug-dir");
      } else if (arg == "--debug-every") {
        options.debugFrameEvery = std::stoi(next("--debug-every"));
      } else if (arg == "--min-hits") {
        options.minTrackHits = std::stoi(next("--min-hits"));
      } else if (arg == "--stride") {
        options.stride = std::stoi(next("--stride"));
      } else if (arg == "--max-frames") {
        options.maxFrames = std::stoi(next("--max-frames"));
      } else if (arg == "--no-ocr") {
        options.enableOcr = false;
      } else if (arg == "--no-title") {
        options.enableTitleFallback = false;
      } else if (arg == "--set-codes") {
        setCodesPath = next("--set-codes");
      } else if (arg == "--reads") {
        readsPath = next("--reads");
      } else if (arg == "--parse") {
        parseText = next("--parse");
      } else if (arg == "--build-index") {
        indexMeta = next("--build-index");
        indexOut = next("--build-index");
      } else if (arg == "--lookup") {
        lookupIndex = next("--lookup");
        lookupImage = next("--lookup");
      } else if (arg == "--phash-index") {
        options.phashIndexPath = next("--phash-index");
      } else if (arg == "--ocr-bench") {
        benchMeta = next("--ocr-bench");
      } else if (arg == "--samples") {
        benchSamples = std::stoi(next("--samples"));
      } else if (arg == "--compare") {
        compareA = next("--compare");
        compareB = next("--compare");
      } else if (arg == "--probe-card") {
        probeCard = next("--probe-card");
      } else if (arg == "--verbose") {
        options.verbose = true;
      } else if (arg.size() > 1 && arg[0] == '-') {
        std::cerr << "unknown option: " << arg << "\n" << kUsage;
        return 2;
      } else if (input.empty()) {
        input = arg;
      } else {
        std::cerr << "unexpected argument: " << arg << "\n";
        return 2;
      }
    } catch (const std::exception &e) {
      std::cerr << e.what() << "\n";
      return 2;
    }
  }
  if (!compareA.empty() && !compareB.empty()) {
    const cv::Mat a = cv::imread(compareA, cv::IMREAD_COLOR);
    const cv::Mat b = cv::imread(compareB, cv::IMREAD_COLOR);
    if (a.empty() || b.empty()) {
      std::cerr << "cannot read compare images\n";
      return 1;
    }
    std::printf("phash=%d signature=%.1f\n",
                cardscan::hashDistance(cardscan::perceptualHash(a), cardscan::perceptualHash(b)),
                cardscan::signatureDistance(cardscan::colorSignature(a),
                                            cardscan::colorSignature(b)));
    return 0;
  }
  if (!probeCard.empty()) {
    const cv::Mat card = cv::imread(probeCard, cv::IMREAD_COLOR);
    if (card.empty()) {
      std::cerr << "cannot read image: " << probeCard << "\n";
      return 1;
    }
    cardscan::CardOcr ocr(options.ocr);
    if (!ocr.available()) {
      std::cerr << "ocr unavailable: " << ocr.lastError() << "\n";
      return 1;
    }
    if (!options.debugDir.empty()) ocr.setDebugDir(options.debugDir, "probe");
    const cardscan::CollectorLayout layout = cardscan::locateCollector(card);
    std::printf("layout: found=%d block=%d,%d %dx%d lines=%zu\n", static_cast<int>(layout.found),
                layout.block.x, layout.block.y, layout.block.width, layout.block.height,
                layout.lines.size());
    for (const auto &line : layout.lines) {
      std::printf("  line: %d,%d %dx%d\n", line.box.x, line.box.y, line.box.width, line.box.height);
    }
    const cardscan::OcrRead read = ocr.readCard(card);
    std::printf("hash: %s\n", cardscan::perceptualHash(card).c_str());
    std::printf("read: score=%d display='%s' raw='%s'\n", read.score, read.display().c_str(),
                read.raw.c_str());
    return 0;
  }
  if (!parseText.empty()) {
    const cardscan::OcrRead read = cardscan::parseCollectorText(parseText);
    std::printf("set=%s number=%s total=%s rarity=%s language=%s score=%d identity=%s\n",
                read.setCode.c_str(), read.number.c_str(), read.total.c_str(),
                read.rarity.c_str(), read.language.c_str(), read.score,
                read.identityKey().c_str());
    return 0;
  }
  if (!benchMeta.empty()) {
    return cardscan::runOcrBenchmark(benchMeta, benchSamples);
  }
  if (!indexMeta.empty()) {
    if (!cardscan::buildHashIndex(indexMeta, indexOut)) {
      std::cerr << "cannot build the hash index from " << indexMeta << "\n";
      return 1;
    }
    std::printf("index written: %s\n", indexOut.c_str());
    return 0;
  }
  if (!lookupIndex.empty()) {
    const cv::Mat card = cv::imread(lookupImage, cv::IMREAD_COLOR);
    if (card.empty()) {
      std::cerr << "cannot read image: " << lookupImage << "\n";
      return 1;
    }
    cardscan::HashIndex index;
    if (!index.load(lookupIndex)) {
      std::cerr << "cannot read index: " << lookupIndex << "\n";
      return 1;
    }
    const std::vector<cardscan::IndexMatch> matches = index.lookupCard(card, 10);
    for (size_t i = 0; i < matches.size(); ++i) {
      std::printf("%2zu. art=%d full=%d  %s %s  %s\n", i + 1, matches[i].artDistance,
                  matches[i].fullDistance, matches[i].setCode.c_str(),
                  matches[i].collector.c_str(), matches[i].name.c_str());
    }
    return 0;
  }
  if (input.empty() && readsPath.empty()) {
    std::cerr << kUsage;
    return 2;
  }

  options.overlayPath = overlayPath;
  if (!setCodesPath.empty()) {
    std::ifstream in(setCodesPath);
    if (!in) {
      std::cerr << "cannot read set codes: " << setCodesPath << "\n";
      return 1;
    }
    std::string token;
    while (in >> token) {
      std::string code;
      for (char c : token) {
        if (std::isalnum(static_cast<unsigned char>(c))) {
          code.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
        } else if (!code.empty()) {
          options.ocr.knownSetCodes.push_back(code);
          code.clear();
        }
      }
      if (!code.empty()) options.ocr.knownSetCodes.push_back(code);
    }
  }
  cardscan::Analyzer analyzer(options);

  std::string json;
  std::string summary;
  if (!readsPath.empty()) {
    const std::vector<cardscan::ReadSample> reads = cardscan::loadReadTimeline(readsPath);
    cardscan::HashIndex index;
    if (!options.phashIndexPath.empty() && !index.load(options.phashIndexPath)) {
      std::cerr << "cannot read the image-hash index: " << options.phashIndexPath << "\n";
      return 1;
    }
    cardscan::VideoResult result;
    result.input = readsPath;
    result.reads = reads;
    result.readsTotal = static_cast<int>(reads.size());
    for (const auto &r : reads) {
      if (!r.identity.empty()) ++result.readsWithIdentity;
    }
    result.cards = cardscan::buildCardsFromReads(reads, options, index.empty() ? nullptr : &index);
    cardscan::consolidateCards(result.cards);    for (const auto &c : result.cards) {
      result.frames = std::max(result.frames, c.lastFrame);
    }
    json = cardscan::videoResultJson(result);
    summary = cardscan::formatVideoSummary(result);
    if (!jsonPath.empty()) {
      std::ofstream out(jsonPath);
      if (!out) {
        std::cerr << "cannot write json: " << jsonPath << "\n";
        return 1;
      }
      out << json;
    }
    std::cout << summary;
    return 0;
  }

  const bool image = cardscan::hasImageExtension(input);
  if (image) {
    const cardscan::ImageResult result = analyzer.analyzeImage(input);
    json = cardscan::imageResultJson(result);
    summary = cardscan::formatImageSummary(result);
    if (!overlayPath.empty()) writeOverlayImage(input, overlayPath, result);
  } else {
    const cardscan::VideoResult result = analyzer.analyzeVideo(input);
    json = cardscan::videoResultJson(result);
    summary = cardscan::formatVideoSummary(result);
  }

  if (!jsonPath.empty()) {
    std::ofstream out(jsonPath);
    if (!out) {
      std::cerr << "cannot write json: " << jsonPath << "\n";
      return 1;
    }
    out << json;
  }
  std::cout << summary;
  return 0;
}

}  // namespace

int main(int argc, char **argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception &e) {
    std::cerr << "error: " << e.what() << "\n";
    return 1;
  }
}
