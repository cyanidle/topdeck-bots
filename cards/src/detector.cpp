#include "detector.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include <opencv2/imgproc.hpp>
#include <opencv2/ximgproc.hpp>

namespace cardscan {
namespace {

constexpr double kCardAspect = 63.0 / 88.0;

struct LineSegment {
  cv::Point2f a;
  cv::Point2f b;
  double angle = 0.0;
  double length = 0.0;
};

double normAngle(double deg) {
  while (deg < 0.0) deg += 180.0;
  while (deg >= 180.0) deg -= 180.0;
  return deg;
}

double angleDiff(double a, double b) {
  const double d = std::abs(normAngle(a) - normAngle(b));
  return std::min(d, 180.0 - d);
}

LineSegment makeSegment(const cv::Point2f &a, const cv::Point2f &b) {
  LineSegment s;
  s.a = a;
  s.b = b;
  const double dx = static_cast<double>(b.x) - a.x;
  const double dy = static_cast<double>(b.y) - a.y;
  s.length = std::hypot(dx, dy);
  s.angle = normAngle(std::atan2(dy, dx) * 180.0 / CV_PI);
  return s;
}

bool lineIntersection(const LineSegment &l1, const LineSegment &l2, cv::Point2f *out) {
  const double x1 = l1.a.x, y1 = l1.a.y, x2 = l1.b.x, y2 = l1.b.y;
  const double x3 = l2.a.x, y3 = l2.a.y, x4 = l2.b.x, y4 = l2.b.y;
  const double denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4);
  if (std::abs(denom) < 1e-6) return false;
  const double t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom;
  out->x = static_cast<float>(x1 + t * (x2 - x1));
  out->y = static_cast<float>(y1 + t * (y2 - y1));
  return true;
}

double pointLineDistance(const LineSegment &l, const cv::Point2f &p) {
  const cv::Point2f d = l.b - l.a;
  const double len = cv::norm(d);
  if (len < 1e-6) return 1e9;
  return std::abs(d.y * (p.x - l.a.x) - d.x * (p.y - l.a.y)) / len;
}

std::vector<LineSegment> detectLines(const cv::Mat &gray, const DetectorParams &p) {
  auto detector = cv::ximgproc::createFastLineDetector(
      std::max(10, p.minLineLength), 1.414f, p.lineCannyLow, p.lineCannyHigh, 3, true);
  std::vector<cv::Vec4f> raw;
  detector->detect(gray, raw);
  std::vector<LineSegment> lines;
  lines.reserve(raw.size());
  for (const auto &v : raw) {
    const LineSegment s = makeSegment(cv::Point2f(v[0], v[1]), cv::Point2f(v[2], v[3]));
    if (s.length < p.minLineLength) continue;
    lines.push_back(s);
  }
  return lines;
}

std::vector<LineSegment> mergeLines(std::vector<LineSegment> lines, const DetectorParams &p) {
  std::sort(lines.begin(), lines.end(),
            [](const LineSegment &a, const LineSegment &b) { return a.length > b.length; });
  std::vector<LineSegment> merged;
  for (const auto &s : lines) {
    bool folded = false;
    for (auto &m : merged) {
      if (angleDiff(s.angle, m.angle) > p.lineAngleTolerance) continue;
      if (pointLineDistance(m, s.a) > p.lineMergeDistance ||
          pointLineDistance(m, s.b) > p.lineMergeDistance)
        continue;
      const cv::Point2f d = (m.b - m.a) * static_cast<float>(1.0 / std::max(1e-6, cv::norm(m.b - m.a)));
      const double ta = (s.a - m.a).dot(d);
      const double tb = (s.b - m.a).dot(d);
      const double lo = std::min({0.0, ta, tb});
      const double hi = std::max({cv::norm(m.b - m.a), ta, tb});
      const cv::Point2f start = m.a + d * static_cast<float>(lo);
      const cv::Point2f end = m.a + d * static_cast<float>(hi);
      m = makeSegment(start, end);
      folded = true;
      break;
    }
    if (!folded) merged.push_back(s);
  }
  std::sort(merged.begin(), merged.end(),
            [](const LineSegment &a, const LineSegment &b) { return a.length > b.length; });
  return merged;
}

double segmentCoverageOnEdge(const LineSegment &s, const cv::Point2f &a, const cv::Point2f &b) {
  const cv::Point2f d = b - a;
  const double len = cv::norm(d);
  if (len < 1.0) return 0.0;
  const cv::Point2f u(d.x / static_cast<float>(len), d.y / static_cast<float>(len));
  auto perpendicular = [&](const cv::Point2f &p) {
    return std::abs(u.x * (p.y - a.y) - u.y * (p.x - a.x));
  };
  if (perpendicular(s.a) > 5.0 || perpendicular(s.b) > 5.0) return 0.0;
  double t1 = (s.a - a).dot(u);
  double t2 = (s.b - a).dot(u);
  if (t1 > t2) std::swap(t1, t2);
  const double lo = std::max(0.0, t1);
  const double hi = std::min(len, t2);
  return hi > lo ? (hi - lo) / len : 0.0;
}

double edgeCoverage(const Quad &q, int edgeIndex, const std::vector<LineSegment> &lines) {
  const cv::Point2f a = q[edgeIndex];
  const cv::Point2f b = q[(edgeIndex + 1) % 4];
  double covered = 0.0;
  for (const auto &s : lines) covered += segmentCoverageOnEdge(s, a, b);
  return std::min(1.0, covered);
}

double cornerProximity(const Quad &q, const std::vector<LineSegment> &lines, double maxDistance) {
  int near = 0;
  for (const auto &c : q) {
    double best = maxDistance;
    for (const auto &s : lines) {
      best = std::min(best, static_cast<double>(cv::norm(s.a - c)));
      best = std::min(best, static_cast<double>(cv::norm(s.b - c)));
    }
    if (best < maxDistance) ++near;
  }
  return near / 4.0;
}

double gradientAt(const cv::Mat &gradient, cv::Point2f p) {
  const int x = cvRound(p.x);
  const int y = cvRound(p.y);
  if (x < 1 || y < 1 || x >= gradient.cols - 1 || y >= gradient.rows - 1) return 0.0;
  return gradient.at<float>(y, x);
}

Quad refineQuadOutward(const cv::Mat &gradient, const Quad &q, double maxOutward,
                       double maxInward) {
  std::array<double, 4> offsets{};
  std::array<LineSegment, 4> shifted{};
  const int binCount = static_cast<int>(maxOutward - (-maxInward)) + 1;
  for (int i = 0; i < 4; ++i) {
    const cv::Point2f a = q[i];
    const cv::Point2f b = q[(i + 1) % 4];
    const double len = cv::norm(b - a);
    if (len < 12.0) return q;
    const cv::Point2f d(static_cast<float>((b.x - a.x) / len), static_cast<float>((b.y - a.y) / len));
    const cv::Point2f n(d.y, -d.x);

    std::vector<int> histogram(binCount, 0);
    int samples = 0;
    for (int s = 2; s <= 22; ++s) {
      const float t = static_cast<float>(s) / 24.0f;
      const cv::Point2f p = a + (b - a) * t;
      double maxAlong = 0.0;
      for (double u = -maxInward; u <= maxOutward; u += 1.0) {
        maxAlong = std::max(maxAlong, gradientAt(gradient, p + n * static_cast<float>(u)));
      }
      if (maxAlong <= 0.0) continue;
      ++samples;
      for (double u = -maxInward; u <= maxOutward; u += 1.0) {
        const double v = gradientAt(gradient, p + n * static_cast<float>(u));
        if (v < 0.35 * maxAlong) continue;
        const double prev = gradientAt(gradient, p + n * static_cast<float>(u - 1.0));
        const double next = gradientAt(gradient, p + n * static_cast<float>(u + 1.0));
        if (v < prev || v <= next) continue;
        const int bin = static_cast<int>(std::round(u + maxInward));
        if (bin >= 0 && bin < binCount) ++histogram[bin];
      }
    }
    if (samples < 6) return q;
    const int required = std::max(3, static_cast<int>(0.4 * samples));
    double bestOffset = 0.0;
    bool found = false;
    for (int bin = binCount - 1; bin >= 0; --bin) {
      if (histogram[bin] < required) continue;
      bestOffset = bin - maxInward;
      found = true;
      break;
    }
    if (!found) return q;
    offsets[i] = bestOffset;
    shifted[i] = makeSegment(a + n * static_cast<float>(offsets[i]),
                             b + n * static_cast<float>(offsets[i]));
  }

  Quad refined;
  for (int i = 0; i < 4; ++i) {
    if (!lineIntersection(shifted[(i + 3) % 4], shifted[i], &refined[i])) return q;
  }
  const double originalArea = polygonArea(q);
  if (polygonArea(refined) < 0.6 * originalArea || polygonArea(refined) > 1.8 * originalArea) {
    return q;
  }
  return refined;
}

double borderSupport(const Quad &q, const cv::Mat &distanceMap, cv::Size frame) {
  double total = 0.0;
  int samples = 0;
  for (int i = 0; i < 4; ++i) {
    const cv::Point2f a = q[i];
    const cv::Point2f b = q[(i + 1) % 4];
    const int steps = 48;
    int hits = 0;
    int valid = 0;
    for (int s = 1; s < steps; ++s) {
      const double t = static_cast<double>(s) / steps;
      const float x = static_cast<float>(a.x + t * (b.x - a.x));
      const float y = static_cast<float>(a.y + t * (b.y - a.y));
      if (x < 0 || y < 0 || x >= frame.width || y >= frame.height) continue;
      ++valid;
      if (distanceMap.at<float>(static_cast<int>(y), static_cast<int>(x)) <= 2.5f) ++hits;
    }
    if (valid < steps / 2) return 0.0;
    total += static_cast<double>(hits) / valid;
    ++samples;
  }
  return samples > 0 ? total / samples : 0.0;
}

bool plausibleQuad(const Quad &q, cv::Size frame, const DetectorParams &p, double *areaOut,
                   double *aspectOut) {
  const double area = polygonArea(q);
  const double frameArea = static_cast<double>(frame.width) * frame.height;
  if (area < p.minAreaFraction * frameArea || area > 0.85 * frameArea) return false;

  const double w = quadWidth(q);
  const double h = quadHeight(q);
  const double minSide = p.minSideFraction * std::min(frame.width, frame.height);
  if (w < minSide || h < minSide) return false;

  const double aspect = h > 1e-6 ? w / h : 0.0;
  if (aspect < p.minAspect || aspect > p.maxAspect) return false;
  if (minCornerAngleDeg(q) < p.minCornerAngleDeg) return false;

  *areaOut = area;
  *aspectOut = aspect;
  return true;
}

void collectFromContours(const std::vector<std::vector<cv::Point>> &contours, cv::Size frame,
                         const DetectorParams &p, std::vector<Detection> &out,
                         std::vector<Quad> *rawQuads, const std::vector<LineSegment> &lines) {
  const double frameArea = static_cast<double>(frame.width) * frame.height;
  for (const auto &c : contours) {
    const double area = cv::contourArea(c);
    if (area < p.minAreaFraction * frameArea) continue;

    std::vector<cv::Point> approx;
    cv::approxPolyDP(c, approx, 0.02 * cv::arcLength(c, true), true);

    std::array<cv::Point2f, 4> rawQuad{};
    bool haveQuad = false;
    if (approx.size() == 4 && cv::isContourConvex(approx)) {
      for (int i = 0; i < 4; ++i) rawQuad[i] = cv::Point2f(approx[i]);
      haveQuad = true;
    } else if (area > 0.05 * frameArea) {
      const cv::RotatedRect rr = cv::minAreaRect(c);
      if (rr.size.width > 20.f && rr.size.height > 20.f) {
        cv::Point2f pts[4];
        rr.points(pts);
        for (int i = 0; i < 4; ++i) rawQuad[i] = pts[i];
        haveQuad = true;
      }
    }
    if (!haveQuad) continue;

    Quad q = orderQuad(rawQuad);
    if (rawQuads) rawQuads->push_back(q);
    double qArea = 0.0, aspect = 0.0;
    if (!plausibleQuad(q, frame, p, &qArea, &aspect)) continue;

    const double c0 = edgeCoverage(q, 0, lines);
    const double c1 = edgeCoverage(q, 1, lines);
    const double c2 = edgeCoverage(q, 2, lines);
    const double c3 = edgeCoverage(q, 3, lines);
    const double coverageMean = 0.25 * (c0 + c1 + c2 + c3);
    const int coveredEdges = static_cast<int>(c0 >= 0.30) + static_cast<int>(c1 >= 0.30) +
                             static_cast<int>(c2 >= 0.30) + static_cast<int>(c3 >= 0.30);
    if (coverageMean < p.minEdgeCoverage || coveredEdges < p.minCoveredEdges) continue;

    Detection d;
    d.quad = q;
    d.area = qArea;
    d.aspect = aspect;
    d.support = coverageMean;
    d.score = 0.9 * coverageMean * std::sqrt(qArea / (static_cast<double>(frame.width) * frame.height)) *
              (1.0 - 0.5 * std::min(1.0, std::abs(aspect - kCardAspect) / kCardAspect));
    d.bbox = quadBoundingRect(q, frame);
    d.source = DetectionSource::Contour;
    out.push_back(d);
  }
}

void suppress(std::vector<Detection> &cands, int maxOut) {
  std::sort(cands.begin(), cands.end(),
            [](const Detection &a, const Detection &b) { return a.score > b.score; });
  std::vector<Detection> kept;
  for (const auto &c : cands) {
    bool drop = false;
    for (const auto &k : kept) {
      if (quadIoU(c.quad, k.quad) > 0.90 || quadContainment(c.quad, k.quad) > 0.98) {
        drop = true;
        break;
      }
    }
    if (!drop) {
      kept.push_back(c);
      if (static_cast<int>(kept.size()) >= maxOut) break;
    }
  }
  cands.swap(kept);
}

}  // namespace

std::vector<Detection> CardDetector::detect(const cv::Mat &bgr, cv::Mat *edgeDebug,
                                            std::vector<Quad> *rawQuads) const {
  if (bgr.empty()) return {};

  double scale = 1.0;
  cv::Mat work = bgr;
  const int maxSide = std::max(bgr.cols, bgr.rows);
  if (maxSide > p_.workMaxSide) {
    scale = static_cast<double>(p_.workMaxSide) / maxSide;
    cv::resize(bgr, work, cv::Size(), scale, scale, cv::INTER_AREA);
  }

  cv::Mat gray;
  cv::cvtColor(work, gray, cv::COLOR_BGR2GRAY);
  cv::Mat blurred;
  cv::GaussianBlur(gray, blurred, cv::Size(5, 5), 0);

  cv::Mat edges;
  cv::Canny(blurred, edges, p_.cannyLow, p_.cannyHigh);
  if (edgeDebug) cv::cvtColor(edges, *edgeDebug, cv::COLOR_GRAY2BGR);

  cv::Mat edgesInv;
  cv::bitwise_not(edges, edgesInv);
  cv::Mat distanceMap;
  cv::distanceTransform(edgesInv, distanceMap, cv::DIST_L2, 3);

  cv::Mat sobelX, sobelY, gradient;
  cv::Sobel(blurred, sobelX, CV_32F, 1, 0, 3);
  cv::Sobel(blurred, sobelY, CV_32F, 0, 1, 3);
  cv::magnitude(sobelX, sobelY, gradient);

  std::vector<Detection> cands;

  const std::vector<LineSegment> lines = mergeLines(detectLines(gray, p_), p_);
  std::vector<LineSegment> horizontal, vertical;
  for (const auto &l : lines) {
    if (l.angle < 45.0 || l.angle >= 135.0) {
      horizontal.push_back(l);
    } else {
      vertical.push_back(l);
    }
  }
  const int maxPerOrientation = std::max(2, p_.maxLinesPerOrientation);
  if (static_cast<int>(horizontal.size()) > maxPerOrientation) horizontal.resize(maxPerOrientation);
  if (static_cast<int>(vertical.size()) > maxPerOrientation) vertical.resize(maxPerOrientation);

  for (size_t i = 0; i < horizontal.size(); ++i) {
    for (size_t j = i + 1; j < horizontal.size(); ++j) {
      for (size_t k = 0; k < vertical.size(); ++k) {
        for (size_t l = k + 1; l < vertical.size(); ++l) {
          cv::Point2f p1, p2, p3, p4;
          if (!lineIntersection(horizontal[i], vertical[k], &p1)) continue;
          if (!lineIntersection(horizontal[j], vertical[k], &p2)) continue;
          if (!lineIntersection(horizontal[j], vertical[l], &p3)) continue;
          if (!lineIntersection(horizontal[i], vertical[l], &p4)) continue;

          Quad q = orderQuad({p1, p2, p3, p4});
          if (rawQuads) rawQuads->push_back(q);

          double area = 0.0, aspect = 0.0;
          if (!plausibleQuad(q, work.size(), p_, &area, &aspect)) continue;

          const double support = borderSupport(q, distanceMap, work.size());
          const double c0 = edgeCoverage(q, 0, lines);
          const double c1 = edgeCoverage(q, 1, lines);
          const double c2 = edgeCoverage(q, 2, lines);
          const double c3 = edgeCoverage(q, 3, lines);
          const double coverageMean = 0.25 * (c0 + c1 + c2 + c3);
          const int coveredEdges = static_cast<int>(c0 >= 0.30) + static_cast<int>(c1 >= 0.30) +
                                   static_cast<int>(c2 >= 0.30) + static_cast<int>(c3 >= 0.30);
          if (coverageMean < p_.minEdgeCoverage || coveredEdges < p_.minCoveredEdges) continue;
          const double cornerFactor = cornerProximity(q, lines, 20.0);
          if (cornerFactor < 0.5) continue;

          const double cardDim = std::min(quadWidth(q), quadHeight(q));
          const Quad refined =
              refineQuadOutward(gradient, q, 0.13 * cardDim, 0.01 * cardDim);
          double refinedArea = 0.0, refinedAspect = 0.0;
          if (plausibleQuad(refined, work.size(), p_, &refinedArea, &refinedAspect)) {
            if (std::getenv("CARDSCAN_DEBUG_DETECT")) {
              std::fprintf(stderr,
                           "[det] raw=(%.0f,%.0f)(%.0f,%.0f)(%.0f,%.0f)(%.0f,%.0f) "
                           "refined=(%.0f,%.0f)(%.0f,%.0f)(%.0f,%.0f)(%.0f,%.0f) "
                           "area %.0f -> %.0f\n",
                           q[0].x, q[0].y, q[1].x, q[1].y, q[2].x, q[2].y, q[3].x, q[3].y,
                           refined[0].x, refined[0].y, refined[1].x, refined[1].y, refined[2].x,
                           refined[2].y, refined[3].x, refined[3].y, area, refinedArea);
            }
            q = refined;
            area = refinedArea;
            aspect = refinedAspect;
          }

          Detection d;
          d.quad = q;
          d.area = area;
          d.aspect = aspect;
          d.support = coverageMean;
          d.source = DetectionSource::Lines;
          d.score = coverageMean * (0.5 + 0.5 * support) * (0.4 + 0.6 * cornerFactor) *
                    std::sqrt(area / (static_cast<double>(work.cols) * work.rows)) *
                    (1.0 - 0.5 * std::min(1.0, std::abs(aspect - kCardAspect) / kCardAspect));
          d.bbox = quadBoundingRect(q, work.size());
          cands.push_back(d);
        }
      }
    }
  }

  if (p_.useContourFallback) {
    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(edges, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);
    collectFromContours(contours, work.size(), p_, cands, rawQuads, lines);
  }

  suppress(cands, p_.maxCandidates);

  if (rawQuads) {
    for (auto &q : *rawQuads) q = scaleQuad(q, 1.0 / scale);
  }
  for (auto &d : cands) {
    const cv::Rect bbox = quadBoundingRect(scaleQuad(d.quad, 1.0 / scale), bgr.size());
    d.quad = scaleQuad(d.quad, 1.0 / scale);
    d.area = d.area / (scale * scale);
    d.bbox = bbox;
  }
  return cands;
}

}  // namespace cardscan
