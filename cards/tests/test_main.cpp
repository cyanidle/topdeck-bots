#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "analyzer.h"
#include "detector.h"
#include "geometry.h"
#include "identity.h"
#include "tracker.h"

namespace {

int gFailures = 0;
int gChecks = 0;

void check(bool condition, const std::string &what) {
  ++gChecks;
  if (!condition) {
    ++gFailures;
    std::fprintf(stderr, "FAIL: %s\n", what.c_str());
  }
}

void checkNear(double a, double b, double eps, const std::string &what) {
  check(std::fabs(a - b) <= eps, what + " (" + std::to_string(a) + " vs " + std::to_string(b) + ")");
}

cardscan::Quad makeQuad(float x, float y, float w, float h) {
  return {cv::Point2f(x, y), cv::Point2f(x + w, y), cv::Point2f(x + w, y + h),
          cv::Point2f(x, y + h)};
}

void testOrderQuad() {
  const std::array<cv::Point2f, 4> shuffled = {cv::Point2f(100, 300), cv::Point2f(10, 20),
                                               cv::Point2f(100, 20), cv::Point2f(10, 300)};
  const auto q = cardscan::orderQuad(shuffled);
  checkNear(q[0].x, 10, 0.01, "orderQuad tl x");
  checkNear(q[0].y, 20, 0.01, "orderQuad tl y");
  checkNear(q[1].x, 100, 0.01, "orderQuad tr x");
  checkNear(q[2].y, 300, 0.01, "orderQuad br y");
  checkNear(q[3].x, 10, 0.01, "orderQuad bl x");

  const std::array<cv::Point2f, 4> rotated = {cv::Point2f(50, 0), cv::Point2f(0, 40),
                                              cv::Point2f(-50, 0), cv::Point2f(0, -40)};
  const auto rq = cardscan::orderQuad(rotated);
  check(rq.size() == 4, "orderQuad keeps four corners");
  checkNear(cardscan::polygonArea(rq), 4000.0, 200.0, "orderQuad keeps area");
}

void testGeometry() {
  const auto q = makeQuad(0, 0, 63, 88);
  checkNear(cardscan::quadAspect(q), 63.0 / 88.0, 1e-6, "quadAspect of 63x88 card");
  checkNear(cardscan::polygonArea(q), 63.0 * 88.0, 1e-6, "polygonArea");
  checkNear(cardscan::quadIoU(q, q), 1.0, 1e-6, "quadIoU with itself");
  checkNear(cardscan::quadIoU(q, makeQuad(63, 0, 63, 88)), 0.0, 1e-6, "quadIoU disjoint");
  check(cardscan::quadContainment(makeQuad(10, 10, 20, 20), q) > 0.99, "containment");
  checkNear(cardscan::minCornerAngleDeg(q), 90.0, 1e-6, "right angles");

  const cv::Mat warped = cardscan::warpQuad(cv::Mat(200, 200, CV_8UC3, cv::Scalar(1, 2, 3)), q,
                                            63, 88);
  check(warped.cols == 63 && warped.rows == 88, "warpQuad size");
}

cardscan::OcrRead parse(const std::string &text) { return cardscan::parseCollectorText(text); }

void testParsing() {
  {
    const auto r = parse("051/065 C DDS \xe2\x80\xa2 EN ARNIE SWEKEL");
    check(r.number == "051", "parse collector number");
    check(r.total == "065", "parse collector total");
    check(r.rarity == "C", "parse rarity");
    check(r.setCode == "DDS", "parse set code");
    check(r.language == "EN", "parse language");
    check(r.identityKey() == "DDS:051", "parse identity key");
    check(r.score >= 10, "parse score high");
  }
  {
    const auto r = parse("150/307 R C18 \xc2\xb7 EN SAMI PRISANEN");
    check(r.number == "150" && r.total == "307", "parse second collector number");
    check(r.setCode == "C18", "parse alphanumeric set code");
    check(r.language == "EN", "parse language after dot separator");
    check(r.rarity == "R", "parse rarity R");
  }
  {
    const auto r = parse("0511065 C DDS EN");
    check(r.number == "051" && r.total == "065", "parse number with '1' instead of slash");
    check(r.setCode == "DDS", "parse set without separator");
  }
  {
    const auto r = parse("Sylvan Might");
    check(r.number.empty() && r.setCode.empty(), "title is not parsed as collector line");
  }
  {
    const auto r = parse("001/280 M PL");
    check(r.number == "001" && r.total == "280", "leading zeros kept");
    check(r.rarity == "M", "rarity M");
    check(r.setCode.empty(), "no set when absent");
  }
  {
    const auto r = parse("150/307 R C18 - EN");
    check(r.setCode == "C18" && r.language == "EN", "set with dash separator");
  }
  {
    const auto r = parse("C18 \xe2\x80\xa2 EN");
    check(r.setCode == "C18" && r.language == "EN", "set line without collector number");
  }
  check(cardscan::parseCollectorText("").score == 0, "empty text scores zero");
}

void testHashHelpers() {
  check(cardscan::hashDistance("00", "00") == 0, "hash distance identical");
  check(cardscan::hashDistance("ff", "00") == 8, "hash distance all bits");
  check(cardscan::hashDistance("0f", "f0") == 8, "hash distance nibbles");
  check(cardscan::hashDistance("", "00") > 100, "hash distance empty");
}

void testTracker() {
  cardscan::Tracker tracker;
  cardscan::Detection d;
  d.quad = makeQuad(100, 100, 200, 280);
  d.area = 56000;
  d.aspect = 200.0 / 280.0;

  const int id1 = tracker.update(0, 0.0, d, {});
  cardscan::Detection moved = d;
  moved.quad = cardscan::translateQuad(d.quad, cv::Point2f(5.f, 3.f));
  const int id2 = tracker.update(1, 0.033, moved, {});
  check(id1 == id2, "tracker keeps identity for a moving card");
  check(tracker.get(id1)->hits == 2, "tracker counts hits");

  cardscan::Detection other = d;
  other.quad = makeQuad(500, 900, 200, 280);
  other.area = 56000;
  other.aspect = 200.0 / 280.0;
  const int id3 = tracker.update(2, 0.066, other, {});
  check(id3 != id1, "tracker splits distant cards");
  tracker.endFrame(2);
  check(tracker.get(id3)->misses == 0, "matched track has no misses");
}

void testTrackerGap() {
  cardscan::Tracker tracker;
  cardscan::Tracker::Params params;
  params.maxGapFrames = 2;
  tracker = cardscan::Tracker(params);
  cardscan::Detection d;
  d.quad = makeQuad(100, 100, 200, 280);
  d.area = 56000;
  const int id = tracker.update(0, 0.0, d, {});
  const int later = tracker.update(10, 0.33, d, {});
  check(later != id, "tracker starts a new track after a long gap");
}

}  // namespace

int main() {
  testOrderQuad();
  testGeometry();
  testParsing();
  testHashHelpers();
  testTracker();
  testTrackerGap();
  std::printf("%d checks, %d failures\n", gChecks, gFailures);
  return gFailures == 0 ? 0 : 1;
}
