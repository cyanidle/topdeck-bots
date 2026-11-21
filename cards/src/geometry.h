#pragma once

#include <array>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

namespace cardscan {

using Quad = std::array<cv::Point2f, 4>;

Quad orderQuad(const std::array<cv::Point2f, 4> &pts);
double polygonArea(const Quad &q);
double edgeLength(const cv::Point2f &a, const cv::Point2f &b);
double quadWidth(const Quad &q);
double quadHeight(const Quad &q);
double quadAspect(const Quad &q);
double quadIoU(const Quad &a, const Quad &b);
double quadContainment(const Quad &inner, const Quad &outer);
double minCornerAngleDeg(const Quad &q);
Quad scaleQuad(const Quad &q, double s);
Quad translateQuad(const Quad &q, cv::Point2f d);
cv::Rect quadBoundingRect(const Quad &q, cv::Size clampTo);
bool allCornersInside(const Quad &q, cv::Size size, float margin);
cv::Mat warpQuad(const cv::Mat &src, const Quad &quad, int outWidth, int outHeight,
                 int interpolation = cv::INTER_CUBIC);

}  // namespace cardscan
