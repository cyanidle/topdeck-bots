#include "ocrbench.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <numeric>
#include <string>
#include <vector>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "analyzer.h"
#include "identity.h"
#include "ocr.h"

namespace cardscan {
namespace {

struct Sample {
  std::string setCode;
  std::string collector;
  std::string path;
};

struct Variant {
  int mode = 0;
  int psm = 7;
  int target = 260;
  int upscaleFor(int rows) const {
    const int up = static_cast<int>(std::lround(static_cast<double>(target) / std::max(1, rows)));
    return std::clamp(up, 2, 16);
  }
  std::string label() const {
    return "mode" + std::to_string(mode) + "_psm" + std::to_string(psm) + "_t" +
           std::to_string(target);
  }
};

std::string jsonField(const std::string &line, const char *key) {
  const std::string token = std::string("\"") + key + "\":";
  const size_t at = line.find(token);
  if (at == std::string::npos) return {};
  size_t start = at + token.size();
  while (start < line.size() && (line[start] == ' ' || line[start] == '\t')) ++start;
  const bool quoted = start < line.size() && line[start] == '"';
  if (quoted) ++start;
  size_t end = start;
  if (quoted) {
    while (end < line.size() && line[end] != '"') ++end;
  } else {
    while (end < line.size() && line[end] != ',' && line[end] != '}') ++end;
  }
  return line.substr(start, end - start);
}

std::vector<Sample> loadSamples(const std::string &metaPath, int limit) {
  std::vector<Sample> samples;
  std::ifstream in(metaPath);
  if (!in) return samples;
  std::string line;
  while (std::getline(in, line)) {
    Sample sample;
    sample.setCode = jsonField(line, "set");
    sample.collector = jsonField(line, "collector");
    sample.path = jsonField(line, "path");
    if (sample.setCode.empty() || sample.collector.empty() || sample.path.empty()) continue;
    samples.push_back(std::move(sample));
  }
  if (limit > 0 && static_cast<int>(samples.size()) > limit) {
    std::vector<Sample> picked;
    const int step = std::max(1, static_cast<int>(samples.size()) / limit);
    for (size_t i = 0; i < samples.size() && static_cast<int>(picked.size()) < limit; i += step) {
      picked.push_back(samples[i]);
    }
    samples.swap(picked);
  }
  return samples;
}

// Cuts the collector line out of a clean Scryfall image and degrades it to what the
// detector sees on a 720p video: a 10-16 px tall line, motion blur and sensor noise.
// The `small` cache image is 146 px wide, so it is scaled up first (the relative ranking
// of the variants is what this benchmark measures).
cv::Mat degradeCollectorLine(const cv::Mat &card, double scale, double blurSigma) {
  if (card.empty()) return {};
  cv::Mat big;
  cv::resize(card, big, cv::Size(), scale, scale, cv::INTER_LANCZOS4);
  const int x = static_cast<int>(big.cols * 0.020);
  const int y = static_cast<int>(big.rows * 0.875);
  const int w = std::max(24, static_cast<int>(big.cols * 0.60));
  const int h = std::max(8, static_cast<int>(big.rows * 0.105));
  const cv::Rect box = cv::Rect(x, y, w, h) & cv::Rect(0, 0, big.cols, big.rows);
  cv::Mat line = big(box).clone();
  cv::GaussianBlur(line, line, cv::Size(0, 0), blurSigma);
  cv::Mat noise(line.size(), CV_8UC3);
  cv::randn(noise, cv::Scalar(0, 0, 0), cv::Scalar(5, 5, 5));
  line.convertTo(line, CV_8UC3);
  cv::add(line, noise, line, cv::noArray(), CV_8UC3);
  if (std::getenv("CARDSCAN_DEBUG_OCR")) cv::imwrite("/tmp/bench_line.png", line);
  return line;
}

}  // namespace

int runOcrBenchmark(const std::string &metaPath, int samples) {
  const std::vector<Sample> data = loadSamples(metaPath, samples);
  if (data.empty()) {
    std::fprintf(stderr, "no samples: %s\n", metaPath.c_str());
    return 1;
  }
  CardOcr ocr;
  if (!ocr.available()) {
    std::fprintf(stderr, "ocr unavailable: %s\n", ocr.lastError().c_str());
    return 1;
  }

  std::vector<Variant> variants;
  for (int mode = 0; mode <= 2; ++mode) {
    for (int psm : {6, 7}) {
      for (int target : {200, 320}) variants.push_back({mode, psm, target});
    }
  }
  std::vector<int> numberHits(variants.size(), 0);
  std::vector<int> setHits(variants.size(), 0);
  std::vector<int> identityHits(variants.size(), 0);
  int used = 0;

  for (const auto &sample : data) {
    const cv::Mat card = cv::imread(sample.path, cv::IMREAD_COLOR);
    if (card.empty()) continue;
    for (double scale : {3.0, 4.2}) {
      for (double blurSigma : {0.6, 1.1}) {
        const cv::Mat line = degradeCollectorLine(card, scale, blurSigma);
        if (line.empty()) continue;
        ++used;
        for (size_t v = 0; v < variants.size(); ++v) {
          const std::string text = ocr.readLineVariant(
              line, variants[v].mode, variants[v].psm, variants[v].upscaleFor(line.rows));
          if (std::getenv("CARDSCAN_DEBUG_OCR") && v == 0) {
            std::fprintf(stderr, "[bench] %s %s -> '%s'\n", sample.setCode.c_str(),
                         sample.collector.c_str(), text.c_str());
          }
          const OcrRead read = parseCollectorText(text);
          const bool numberOk =
              normalizeCollector(read.number) == normalizeCollector(sample.collector);
          const bool setOk = read.setCode == sample.setCode;
          if (numberOk) ++numberHits[v];
          if (setOk) ++setHits[v];
          if (numberOk && setOk) ++identityHits[v];
        }
      }
    }
    if (used % 20 == 0) std::fprintf(stderr, "  %d samples\n", used);
  }
  if (used == 0) return 1;

  std::vector<size_t> order(variants.size());
  std::iota(order.begin(), order.end(), 0);
  std::sort(order.begin(), order.end(),
            [&](size_t a, size_t b) { return identityHits[a] > identityHits[b]; });
  std::printf("%-22s %8s %8s %8s\n", "variant", "number%", "set%", "identity%");
  for (size_t index : order) {
    std::printf("%-22s %7.1f%% %7.1f%% %7.1f%%\n", variants[index].label().c_str(),
                100.0 * numberHits[index] / used, 100.0 * setHits[index] / used,
                100.0 * identityHits[index] / used);
  }
  std::printf("samples: %d\n", used);
  return 0;
}

}  // namespace cardscan
