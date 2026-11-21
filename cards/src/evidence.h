#pragma once

#include <opencv2/core.hpp>

namespace cardscan {

struct CardEvidence {
  double edgeDensity = 0.0;
  double borderContrast = 0.0;
  double topBarContrast = 0.0;
};

struct SerialEvidence {
  int glyphs = 0;
  double brightFraction = 0.0;
  double backgroundMean = 0.0;
  double foregroundMean = 0.0;
  bool darkBackground = false;
  bool accepted = false;
  double score = 0.0;
};

CardEvidence analyzeCard(const cv::Mat &crop);
SerialEvidence analyzeSerialLine(const cv::Mat &crop);

}
