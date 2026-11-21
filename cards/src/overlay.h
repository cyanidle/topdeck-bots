#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "geometry.h"

namespace cardscan {

struct OverlayStyle {
  cv::Scalar identified{80, 220, 80};
  cv::Scalar pending{0, 200, 240};
  cv::Scalar unidentified{60, 180, 250};
  cv::Scalar text{255, 255, 255};
  double fontScale = 0.55;
  int thickness = 2;
};

void drawQuad(cv::Mat &canvas, const Quad &quad, const cv::Scalar &color, const std::string &label,
              const OverlayStyle &style);
void drawHud(cv::Mat &canvas, const std::vector<std::string> &lines, const OverlayStyle &style);

}
