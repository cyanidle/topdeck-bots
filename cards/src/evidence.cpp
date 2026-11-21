#include "evidence.h"

#include <algorithm>
#include <cmath>

#include <opencv2/imgproc.hpp>

#include "text_runs.h"

namespace cardscan {
namespace {

double bandMean(const cv::Mat &gray, double inset, double thickness) {
  const int x0 = static_cast<int>(gray.cols * inset);
  const int y0 = static_cast<int>(gray.rows * inset);
  const int tw = std::max(2, static_cast<int>(gray.cols * thickness));
  const int th = std::max(2, static_cast<int>(gray.rows * thickness));
  const cv::Rect top(x0, y0, gray.cols - 2 * x0, th);
  const cv::Rect bottom(x0, gray.rows - y0 - th, gray.cols - 2 * x0, th);
  const cv::Rect left(x0, y0, tw, gray.rows - 2 * y0);
  const cv::Rect right(gray.cols - x0 - tw, y0, tw, gray.rows - 2 * y0);
  const cv::Rect rects[4] = {top, bottom, left, right};
  double sum = 0.0;
  double count = 0.0;
  for (const auto &r : rects) {
    const cv::Rect clipped = r & cv::Rect(0, 0, gray.cols, gray.rows);
    if (clipped.width < 1 || clipped.height < 1) continue;
    sum += cv::mean(gray(clipped))[0] * clipped.area();
    count += clipped.area();
  }
  return count > 0 ? sum / count : 0.0;
}

}  // namespace

CardEvidence analyzeCard(const cv::Mat &crop) {  CardEvidence evidence;
  if (crop.empty()) return evidence;
  cv::Mat gray;
  if (crop.channels() == 3) {
    cv::cvtColor(crop, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = crop;
  }

  cv::Mat edges;
  cv::Canny(gray, edges, 50, 150);
  const cv::Rect inner(static_cast<int>(gray.cols * 0.12), static_cast<int>(gray.rows * 0.12),
                       static_cast<int>(gray.cols * 0.76), static_cast<int>(gray.rows * 0.76));
  if (inner.width > 4 && inner.height > 4) {
    evidence.edgeDensity = cv::countNonZero(edges(inner)) / static_cast<double>(inner.area());
  }

  const double outer = bandMean(gray, 0.004, 0.012);
  const double border = bandMean(gray, 0.030, 0.015);
  const double frame = bandMean(gray, 0.065, 0.015);
  evidence.borderContrast = std::abs(outer - border) / 255.0;
  evidence.topBarContrast = std::abs(border - frame) / 255.0;
  return evidence;
}

SerialEvidence analyzeSerialLine(const cv::Mat &crop) {
  SerialEvidence evidence;
  if (crop.empty()) return evidence;
  cv::Mat gray;
  if (crop.channels() == 3) {
    cv::cvtColor(crop, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = crop;
  }

  const cv::Rect band(static_cast<int>(crop.cols * 0.010), static_cast<int>(crop.rows * 0.880),
                      static_cast<int>(crop.cols * 0.560), static_cast<int>(crop.rows * 0.115));
  const cv::Rect roi = band & cv::Rect(0, 0, crop.cols, crop.rows);
  if (roi.width < 30 || roi.height < 12) return evidence;

  cv::Mat up;
  const double factor = std::clamp(240.0 / roi.height, 2.0, 8.0);
  cv::resize(gray(roi), up, cv::Size(), factor, factor, cv::INTER_CUBIC);
  cv::Mat blurred;
  cv::GaussianBlur(up, blurred, cv::Size(3, 3), 0);
  cv::Mat binary;
  cv::threshold(blurred, binary, 0, 255, cv::THRESH_BINARY | cv::THRESH_OTSU);

  const double whiteFraction = cv::mean(binary)[0] / 255.0;
  if (whiteFraction > 0.5) cv::bitwise_not(binary, binary);
  evidence.brightFraction = whiteFraction;

  cv::Mat mask = binary == 255;
  evidence.backgroundMean = cv::mean(blurred, mask)[0];
  cv::Mat inverseMask = binary != 255;
  evidence.foregroundMean = cv::mean(blurred, inverseMask)[0];
  evidence.darkBackground = evidence.backgroundMean > evidence.foregroundMean;
  if (!evidence.darkBackground) return evidence;
  if (evidence.backgroundMean < 60.0) return evidence;

  std::vector<std::vector<cv::Point>> contours;
  cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
  int glyphs = 0;
  for (const auto &c : contours) {
    const cv::Rect b = cv::boundingRect(c);
    if (b.x <= 1 || b.y <= 1 || b.x + b.width >= mask.cols - 1 ||
        b.y + b.height >= mask.rows - 1)
      continue;
    const double hr = static_cast<double>(b.height) / mask.rows;
    const double wr = static_cast<double>(b.width) / mask.cols;
    if (hr < 0.06 || hr > 0.42) continue;
    if (wr < 0.003 || wr > 0.06) continue;
    if (cv::contourArea(c) < 8.0) continue;
    ++glyphs;
  }
  evidence.glyphs = glyphs;
  evidence.score = std::min(1.0, glyphs / 8.0);
  evidence.accepted = glyphs >= 4;
  return evidence;
}

}
