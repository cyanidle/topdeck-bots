#pragma once

#include <string>

#include <opencv2/core.hpp>

#include "identity.h"

namespace cardscan {

struct OcrParams {
  int cardWidth = 630;
  int cardHeight = 880;
  int upscale = 4;
  std::string language = "eng";
  std::string dataPath;
  int maxAttemptsPerRead = 10;
  double collectorRoiTop = 0.72;
  double collectorRoiWidth = 0.62;
  double collectorMaxStartX = 0.45;
  // How far below the detected quad the collector line may sit, in card heights: the detector
  // stops at the inner edge of the black border, so the card's own line lands a little below
  // that edge, while the card lying behind shows its line much further down.
  double contentMargin = 0.05;
  std::vector<std::string> knownSetCodes;
};

struct CardRegions {
  cv::Rect collectorLine;      // "051/065 C"
  cv::Rect setLine;            // "DDS * EN  artist"
  cv::Rect collectorBlock;     // both lines
  cv::Rect title;              // title bar
};

struct TextLineBox {
  cv::Rect box;
};

struct CollectorLayout {
  bool found = false;
  cv::Rect block;
  std::vector<TextLineBox> lines;
};

CardRegions defaultRegions(const OcrParams &p);
CollectorLayout locateCollector(const cv::Mat &cardWarped, const OcrParams &p = OcrParams(),
                                double contentBottom = 1.0);

class CardOcr {
 public:
  explicit CardOcr(const OcrParams &p = OcrParams());
  ~CardOcr();

  CardOcr(const CardOcr &) = delete;
  CardOcr &operator=(const CardOcr &) = delete;

  bool available() const { return api_ != nullptr; }
  const std::string &lastError() const { return error_; }
  const OcrParams &params() const { return p_; }

  OcrRead readCard(const cv::Mat &cardWarped, double contentBottom = 1.0);
  std::string readTitle(const cv::Mat &cardWarped);

  // Benchmark hook: reads one isolated collector-line crop with an explicit variant.
  std::string readLineVariant(const cv::Mat &line, int mode, int psm, int upscale);

  void setDebugDir(const std::string &dir, const std::string &prefix = "ocr") {
    debugDir_ = dir;
    debugPrefix_ = prefix;
  }

 private:
  std::string run(const cv::Mat &gray, int psm);
  static cv::Mat prepare(const cv::Mat &gray, int mode, int upscale);

  OcrParams p_;
  CardRegions regions_;
  void *api_ = nullptr;
  std::string error_;
  std::string debugDir_;
  std::string debugPrefix_ = "ocr";
  std::string lastRawLog_;
};

}
