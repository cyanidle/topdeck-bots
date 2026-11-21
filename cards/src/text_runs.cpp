#include "text_runs.h"

#include <algorithm>
#include <vector>

#include <opencv2/imgproc.hpp>

namespace cardscan {

int countTextRunsInGray(const cv::Mat &gray) {
  if (gray.empty() || gray.cols < 16 || gray.rows < 10) return 0;
  const double factor = std::clamp(160.0 / gray.rows, 2.0, 8.0);
  cv::Mat up;
  cv::resize(gray, up, cv::Size(), factor, factor, cv::INTER_CUBIC);
  cv::Mat blurred;
  cv::GaussianBlur(up, blurred, cv::Size(3, 3), 0);
  cv::Mat binary;
  cv::threshold(blurred, binary, 0, 255, cv::THRESH_BINARY | cv::THRESH_OTSU);
  if (cv::mean(binary)[0] > 127.0) cv::bitwise_not(binary, binary);

  std::vector<std::vector<cv::Point>> contours;
  cv::findContours(binary, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
  int runs = 0;
  for (const auto &c : contours) {
    const cv::Rect b = cv::boundingRect(c);
    if (b.x <= 1 || b.y <= 1 || b.x + b.width >= binary.cols - 1 ||
        b.y + b.height >= binary.rows - 1)
      continue;
    const double heightRatio = static_cast<double>(b.height) / binary.rows;
    const double widthRatio = static_cast<double>(b.width) / binary.cols;
    if (heightRatio < 0.08 || heightRatio > 0.60) continue;
    if (widthRatio < 0.004 || widthRatio > 0.12) continue;
    if (cv::contourArea(c) < 10.0) continue;
    ++runs;
  }
  return runs;
}

int countTextRuns(const cv::Mat &cardWarped, const cv::Rect &region) {
  if (cardWarped.empty()) return 0;
  const cv::Rect r = region & cv::Rect(0, 0, cardWarped.cols, cardWarped.rows);
  if (r.width < 8 || r.height < 6) return 0;
  cv::Mat gray;
  if (cardWarped.channels() == 3) {
    cv::cvtColor(cardWarped(r), gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = cardWarped(r);
  }
  return countTextRunsInGray(gray);
}

}
