#include "tracker.h"

#include <algorithm>
#include <cmath>

#include "phash.h"

namespace cardscan {
namespace {

cv::Point2f quadCenter(const Quad &q) {
  cv::Point2f c(0.f, 0.f);
  for (const auto &p : q) c += p;
  return c * 0.25f;
}

double centerShift(const Quad &a, const Quad &b) {
  const double scale = std::sqrt(std::max(1.0, polygonArea(b)));
  return cv::norm(quadCenter(a) - quadCenter(b)) / scale;
}

}  // namespace

int Tracker::match(int frame, const Detection &d, const std::string &hash) const {
  int bestId = -1;
  double bestIou = 0.0;
  for (const auto &t : tracks_) {
    if (frame - t.lastFrame > p_.maxGapFrames) continue;
    if (!hash.empty() && !t.lastHash.empty() && hashDistance(hash, t.lastHash) > p_.maxHashDistance)
      continue;
    const double iou = quadIoU(t.lastQuad, d.quad);
    if (iou > bestIou) {
      bestIou = iou;
      bestId = t.id;
    }
  }
  if (bestId >= 0 && bestIou >= p_.minIou) return bestId;

  int nearestId = -1;
  double nearestShift = 1e9;
  for (const auto &t : tracks_) {
    if (frame - t.lastFrame > p_.maxGapFrames) continue;
    if (!hash.empty() && !t.lastHash.empty() && hashDistance(hash, t.lastHash) > p_.maxHashDistance)
      continue;
    const double areaRatio = polygonArea(d.quad) / std::max(1.0, polygonArea(t.lastQuad));
    if (areaRatio < p_.minAreaRatio || areaRatio > 1.0 / p_.minAreaRatio) continue;
    const double shift = centerShift(t.lastQuad, d.quad);
    if (shift < nearestShift) {
      nearestShift = shift;
      nearestId = t.id;
    }
  }
  if (nearestId >= 0 && nearestShift <= p_.maxCenterShift) return nearestId;
  return -1;
}

int Tracker::update(int frame, double ts, const Detection &d, const std::string &hash,
                    int matchedId) {
  if (matchedId <= 0) {
    Track t;
    t.id = nextId_++;
    t.firstFrame = frame;
    t.lastFrame = frame;
    t.firstTs = ts;
    t.lastTs = ts;
    t.hits = 1;
    t.lastQuad = d.quad;
    t.lastHash = hash;
    tracks_.push_back(t);
    return t.id;
  }

  Track *t = get(matchedId);
  if (t) {
    if (frame != t->lastFrame) ++t->hits;
    t->lastFrame = frame;
    t->lastTs = ts;
    t->lastQuad = d.quad;
    t->lastHash = hash;
    t->misses = 0;
  }
  return matchedId;
}

void Tracker::endFrame(int frame) {
  for (auto &t : tracks_) {
    if (t.lastFrame != frame) ++t.misses;
  }
}

Track *Tracker::get(int id) {
  for (auto &t : tracks_) {
    if (t.id == id) return &t;
  }
  return nullptr;
}

}  // namespace cardscan
