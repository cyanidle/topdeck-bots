#include "geometry.h"

#include <algorithm>
#include <cmath>
#include <vector>

#include <opencv2/imgproc.hpp>

namespace cardscan {

Quad orderQuad(const std::array<cv::Point2f, 4> &pts) {
  cv::Point2f c(0.f, 0.f);
  for (const auto &p : pts) c += p;
  c *= 0.25f;
  std::array<cv::Point2f, 4> sorted = pts;
  std::sort(sorted.begin(), sorted.end(), [&c](const cv::Point2f &a, const cv::Point2f &b) {
    return std::atan2(a.y - c.y, a.x - c.x) < std::atan2(b.y - c.y, b.x - c.x);
  });
  int start = 0;
  float best = 1e9f;
  for (int i = 0; i < 4; ++i) {
    const float s = sorted[i].x + sorted[i].y;
    if (s < best) {
      best = s;
      start = i;
    }
  }
  Quad q;
  for (int i = 0; i < 4; ++i) q[i] = sorted[(start + i) % 4];
  return q;
}

double polygonArea(const Quad &q) {
  double a = 0.0;
  for (int i = 0; i < 4; ++i) {
    const cv::Point2f &p = q[i];
    const cv::Point2f &n = q[(i + 1) % 4];
    a += static_cast<double>(p.x) * n.y - static_cast<double>(n.x) * p.y;
  }
  return std::abs(a) * 0.5;
}

double edgeLength(const cv::Point2f &a, const cv::Point2f &b) {
  return std::hypot(static_cast<double>(a.x) - b.x, static_cast<double>(a.y) - b.y);
}

double quadWidth(const Quad &q) { return 0.5 * (edgeLength(q[0], q[1]) + edgeLength(q[3], q[2])); }
double quadHeight(const Quad &q) { return 0.5 * (edgeLength(q[0], q[3]) + edgeLength(q[1], q[2])); }

double quadAspect(const Quad &q) {
  const double h = quadHeight(q);
  return h > 1e-6 ? quadWidth(q) / h : 0.0;
}

double quadIoU(const Quad &a, const Quad &b) {
  std::vector<cv::Point2f> pa(a.begin(), a.end());
  std::vector<cv::Point2f> pb(b.begin(), b.end());
  std::vector<cv::Point2f> inter;
  if (!cv::intersectConvexConvex(pa, pb, inter, true) || inter.empty()) return 0.0;
  std::vector<cv::Point2f> hull;
  cv::convexHull(inter, hull);
  const double i = std::abs(cv::contourArea(hull));
  const double u = polygonArea(a) + polygonArea(b) - i;
  return u > 1e-9 ? i / u : 0.0;
}

double quadContainment(const Quad &inner, const Quad &outer) {
  const double a = polygonArea(inner);
  if (a < 1e-9) return 0.0;
  std::vector<cv::Point2f> pa(inner.begin(), inner.end());
  std::vector<cv::Point2f> pb(outer.begin(), outer.end());
  std::vector<cv::Point2f> inter;
  if (!cv::intersectConvexConvex(pa, pb, inter, true) || inter.empty()) return 0.0;
  std::vector<cv::Point2f> hull;
  cv::convexHull(inter, hull);
  return std::min(1.0, std::abs(cv::contourArea(hull)) / a);
}

double minCornerAngleDeg(const Quad &q) {
  double worst = 180.0;
  for (int i = 0; i < 4; ++i) {
    const cv::Point2f &p = q[(i + 3) % 4];
    const cv::Point2f &c = q[i];
    const cv::Point2f &n = q[(i + 1) % 4];
    cv::Point2f v1 = p - c;
    cv::Point2f v2 = n - c;
    const double n1 = cv::norm(v1), n2 = cv::norm(v2);
    if (n1 < 1e-6 || n2 < 1e-6) return 0.0;
    const double cs = (v1.x * v2.x + v1.y * v2.y) / (n1 * n2);
    const double ang = std::acos(std::clamp(cs, -1.0, 1.0)) * 180.0 / CV_PI;
    worst = std::min(worst, ang);
  }
  return worst;
}

Quad scaleQuad(const Quad &q, double s) {
  Quad r;
  for (int i = 0; i < 4; ++i) r[i] = cv::Point2f(static_cast<float>(q[i].x * s), static_cast<float>(q[i].y * s));
  return r;
}

Quad translateQuad(const Quad &q, cv::Point2f d) {
  Quad r;
  for (int i = 0; i < 4; ++i) r[i] = q[i] + d;
  return r;
}

cv::Rect quadBoundingRect(const Quad &q, cv::Size clampTo) {
  float x0 = q[0].x, y0 = q[0].y, x1 = q[0].x, y1 = q[0].y;
  for (int i = 1; i < 4; ++i) {
    x0 = std::min(x0, q[i].x);
    y0 = std::min(y0, q[i].y);
    x1 = std::max(x1, q[i].x);
    y1 = std::max(y1, q[i].y);
  }
  cv::Rect r(cv::Point(static_cast<int>(std::floor(x0)), static_cast<int>(std::floor(y0))),
             cv::Point(static_cast<int>(std::ceil(x1)), static_cast<int>(std::ceil(y1))));
  return r & cv::Rect(0, 0, clampTo.width, clampTo.height);
}

bool allCornersInside(const Quad &q, cv::Size size, float margin) {
  for (const auto &p : q) {
    if (p.x < margin || p.y < margin || p.x > size.width - 1 - margin || p.y > size.height - 1 - margin)
      return false;
  }
  return true;
}

cv::Mat warpQuad(const cv::Mat &src, const Quad &quad, int outWidth, int outHeight, int interpolation) {
  const Quad dst = {cv::Point2f(0.f, 0.f), cv::Point2f(static_cast<float>(outWidth - 1), 0.f),
                    cv::Point2f(static_cast<float>(outWidth - 1), static_cast<float>(outHeight - 1)),
                    cv::Point2f(0.f, static_cast<float>(outHeight - 1))};
  const cv::Mat m = cv::getPerspectiveTransform(std::vector<cv::Point2f>(quad.begin(), quad.end()),
                                                std::vector<cv::Point2f>(dst.begin(), dst.end()));
  cv::Mat out;
  cv::warpPerspective(src, out, m, cv::Size(outWidth, outHeight), interpolation, cv::BORDER_REPLICATE);
  return out;
}

cv::Mat warpQuadMax(const cv::Mat &src, const Quad &quad, int outWidth, int outHeight) {
  return warpQuad(src, quad, outWidth, outHeight, cv::INTER_CUBIC);
}

}  // namespace cardscan
