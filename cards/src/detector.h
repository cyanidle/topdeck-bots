#pragma once

#include <vector>

#include <opencv2/core.hpp>

#include "geometry.h"

namespace cardscan {

enum class DetectionSource { Lines, Contour };

struct Detection {
  Quad quad;  // ordered: top-left, top-right, bottom-right, bottom-left (source image coords)
  double area = 0.0;
  double aspect = 0.0;
  double score = 0.0;
  double support = 0.0;
  DetectionSource source = DetectionSource::Lines;
  cv::Rect bbox;
};

struct DetectorParams {
  int workMaxSide = 1280;
  double minAreaFraction = 0.012;
  double minAspect = 0.35;
  double maxAspect = 1.05;
  double minSideFraction = 0.10;
  double minCornerAngleDeg = 55.0;
  int maxCandidates = 12;
  double cannyLow = 40.0;
  double cannyHigh = 140.0;
  bool useContourFallback = true;

  int minLineLength = 60;
  double lineAngleTolerance = 8.0;
  double lineMergeDistance = 12.0;
  double lineCannyLow = 25.0;
  double lineCannyHigh = 80.0;
  int maxLinesPerOrientation = 14;
  double linePerpendicularTolerance = 5.0;
  double minEdgeCoverage = 0.55;
  double edgeCoveredThreshold = 0.30;
  double cornerProximityDistance = 20.0;
  double minCornerProximity = 0.5;
  int minCoveredEdges = 3;
};

class CardDetector {
 public:
  CardDetector() = default;
  explicit CardDetector(DetectorParams p) : p_(p) {}

  std::vector<Detection> detect(const cv::Mat &bgr, cv::Mat *edgeDebug = nullptr,
                                std::vector<Quad> *rawQuads = nullptr) const;

  const DetectorParams &params() const { return p_; }

 private:
  DetectorParams p_;
};

}
