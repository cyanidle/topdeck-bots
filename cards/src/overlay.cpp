#include "overlay.h"

#include <algorithm>

#include <opencv2/imgproc.hpp>

namespace cardscan {
namespace {

void drawLabel(cv::Mat &canvas, const std::string &text, cv::Point origin, const cv::Scalar &color,
               const OverlayStyle &style) {
  int baseline = 0;
  const cv::Size size =
      cv::getTextSize(text, cv::FONT_HERSHEY_SIMPLEX, style.fontScale, 1, &baseline);
  cv::Rect box(origin.x, origin.y - size.height - 6, size.width + 8, size.height + 8);
  box &= cv::Rect(0, 0, canvas.cols, canvas.rows);
  if (box.width <= 0 || box.height <= 0) return;
  cv::Mat roi = canvas(box);
  cv::addWeighted(roi, 0.25, cv::Mat(roi.size(), roi.type(), cv::Scalar(0, 0, 0)), 0.75, 0, roi);
  cv::putText(canvas, text, origin + cv::Point(4, -3), cv::FONT_HERSHEY_SIMPLEX, style.fontScale,
              color, 1, cv::LINE_AA);
}

}  // namespace

void drawQuad(cv::Mat &canvas, const Quad &quad, const cv::Scalar &color, const std::string &label,
              const OverlayStyle &style) {
  for (int i = 0; i < 4; ++i) {
    cv::line(canvas, quad[i], quad[(i + 1) % 4], color, style.thickness, cv::LINE_AA);
  }
  for (const auto &p : quad) {
    cv::circle(canvas, p, std::max(3, style.thickness + 1), color, -1, cv::LINE_AA);
  }
  if (!label.empty()) {
    drawLabel(canvas, label, cv::Point(static_cast<int>(quad[0].x), static_cast<int>(quad[0].y)),
              style.text, style);
  }
}

void drawHud(cv::Mat &canvas, const std::vector<std::string> &lines, const OverlayStyle &style) {
  if (lines.empty()) return;
  int baseline = 0;
  int width = 0;
  for (const auto &line : lines) {
    const cv::Size s = cv::getTextSize(line, cv::FONT_HERSHEY_SIMPLEX, style.fontScale, 1, &baseline);
    width = std::max(width, s.width);
  }
  const int lineHeight = 22;
  const int height = static_cast<int>(lines.size()) * lineHeight + 12;
  const cv::Rect box(8, 8, std::min(width + 16, canvas.cols - 16), std::min(height, canvas.rows - 16));
  if (box.width <= 0 || box.height <= 0) return;
  cv::Mat roi = canvas(box);
  cv::addWeighted(roi, 0.2, cv::Mat(roi.size(), roi.type(), cv::Scalar(0, 0, 0)), 0.8, 0.0, roi);
  int y = box.y + 8 + lineHeight - 8;
  for (const auto &line : lines) {
    cv::putText(canvas, line, cv::Point(box.x + 6, y + 4), cv::FONT_HERSHEY_SIMPLEX,
                style.fontScale, style.text, 1, cv::LINE_AA);
    y += lineHeight;
  }
}

}  // namespace cardscan
