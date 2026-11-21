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
CollectorLayout locateCollector(const cv::Mat &cardWarped);

class CardOcr {
 public:
  explicit CardOcr(const OcrParams &p = OcrParams());
  ~CardOcr();

  CardOcr(const CardOcr &) = delete;
  CardOcr &operator=(const CardOcr &) = delete;

  bool available() const { return api_ != nullptr; }
  const std::string &lastError() const { return error_; }
  const OcrParams &params() const { return p_; }

  OcrRead readCard(const cv::Mat &cardWarped);
  std::string readTitle(const cv::Mat &cardWarped);

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
