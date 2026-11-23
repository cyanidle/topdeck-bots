#include <cmath>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>

#include "analyzer.h"
#include "detector.h"
#include "geometry.h"
#include "hashindex.h"
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

cardscan::ReadSample makeReadSample(int frame, double ts, const std::string &code,
                                    const std::string &number, const std::string &total,
                                    double quality = 0.5) {
  cardscan::ReadSample s;
  s.frame = frame;
  s.ts = ts;
  s.quality = quality;
  s.code = code;
  s.number = number;
  s.total = total;
  if (!code.empty() && !number.empty()) s.identity = code + ":" + number;
  return s;
}

void testVoteDigits() {
  check(cardscan::voteDigits({"051", "051", "O51"}) == "051", "voteDigits per-position majority");
  check(cardscan::voteDigits({"051", "051", "65"}) == "051", "voteDigits picks the majority length");
  check(cardscan::voteDigits({"149", "149", "49", "49"}) == "149",
        "voteDigits keeps the leading digit when the lengths tie");
  check(cardscan::voteDigits({"49", "49", "49", "149"}) == "49",
        "voteDigits still follows a clear majority of the shorter read");
  check(cardscan::voteDigits({}).empty(), "voteDigits of nothing is empty");
  check(cardscan::voteDigits({"1234567"}).empty(), "voteDigits rejects absurdly long input");
}

void testMergeReads() {
  cardscan::OcrRead number;
  number.number = "051";
  number.total = "065";
  number.rarity = "C";
  number.regionId = 1;
  number.score = 9;

  cardscan::OcrRead set;
  set.setCode = "DDS";
  set.language = "EN";
  set.regionId = 2;
  set.score = 8;

  cardscan::OcrRead noise;
  noise.number = "861";
  noise.total = "065";
  noise.regionId = 0;
  noise.score = 12;

  const cardscan::OcrRead merged = cardscan::mergeReads({noise, number, set});
  check(merged.number == "051" && merged.total == "065", "mergeReads prefers the collector region");
  check(merged.setCode == "DDS" && merged.language == "EN", "mergeReads takes set + language");
  check(merged.identityKey() == "DDS:051", "mergeReads builds an identity");
}

void testSnapSetCode() {
  const std::vector<std::string> known = {"DDS", "C18", "AKH"};
  check(cardscan::snapSetCode("DOS", known) == "DDS", "snapSetCode repairs a confusable code");
  check(cardscan::snapSetCode("C18", known) == "C18", "snapSetCode keeps an exact code");
  check(cardscan::snapSetCode("ZZZ", known) == "ZZZ", "snapSetCode keeps an unknown code");
  check(cardscan::snapSetCode("DDS", {}) == "DDS", "snapSetCode without a vocabulary");
}

std::vector<cardscan::ReadSample> countingTimeline() {
  std::vector<cardscan::ReadSample> reads;
  for (int i = 0; i < 10; ++i) {
    reads.push_back(makeReadSample(i, i * 0.1, i == 4 ? "DOS" : "DDS", "051", "065"));
  }
  for (int i = 0; i < 10; ++i) {
    reads.push_back(makeReadSample(40 + i, 4.0 + i * 0.1, "C18", "150", "307"));
  }
  for (int i = 0; i < 10; ++i) {
    reads.push_back(makeReadSample(80 + i, 8.0 + i * 0.1, "DDS", "051", "065"));
  }
  return reads;
}

void testCountingFromReads() {
  const cardscan::AnalyzerOptions opt;
  const std::vector<cardscan::ReadSample> reads = countingTimeline();
  std::vector<cardscan::CardResult> cards = cardscan::buildCardsFromReads(reads, opt);
  check(cards.size() == 3, "counting splits on the pause and keeps the shows apart");
  if (cards.size() == 3) {
    check(cards[0].identity == "DDS:51", "counting votes the majority set code");
    check(cards[0].number == "051", "counting keeps the printed collector number");
    check(cards[0].number == "051" && cards[0].total == "065", "counting votes the number");
    check(cards[1].identity == "C18:150", "counting follows the set change");
    check(cards[2].identity == "DDS:51", "counting sees the repeated card again");
    check(cards[0].firstTs < cards[1].firstTs && cards[1].firstTs < cards[2].firstTs,
          "counting keeps chronological order");
  }

  cardscan::consolidateCards(cards, opt);
  check(cards.size() == 3, "consolidation keeps a repeated card that came after another");
  if (cards.size() == 3) {
    check(cards[0].identity == "DDS:51" && cards[2].identity == "DDS:51",
          "the repeat keeps its identity");
    check(cards[0].hits == 10 && cards[2].hits == 10, "the repeat is not folded into the first");
  }
}

cardscan::CardResult makeCard(double firstTs, double lastTs, const std::string &code,
                              const std::string &number) {
  cardscan::CardResult c;
  c.trackId = static_cast<int>(firstTs * 100) + 1;
  c.setCode = code;
  c.number = number;
  c.identity = code + ":" + cardscan::normalizeCollector(number);
  c.identified = true;
  c.identifiedByCodes = true;
  c.confidence = 0.6;
  c.hits = 5;
  c.firstTs = firstTs;
  c.lastTs = lastTs;
  c.firstFrame = static_cast<int>(firstTs * 30);
  c.lastFrame = static_cast<int>(lastTs * 30);
  c.bestTs = firstTs;
  c.bestFrame = c.firstFrame;
  c.mergedTrackIds.push_back(c.trackId);
  return c;
}

void testDuplicateCards() {
  const cardscan::AnalyzerOptions opt;
  {
    std::vector<cardscan::CardResult> cards = {makeCard(0.0, 1.0, "DDS", "051"),
                                               makeCard(1.05, 2.0, "DDS", "051")};
    cardscan::consolidateCards(cards, opt);
    check(cards.size() == 1, "a contiguous duplicate is one physical card");
    if (!cards.empty()) check(cards[0].hits == 10, "contiguous duplicate sums the hits");
  }
  {
    std::vector<cardscan::CardResult> cards = {makeCard(0.0, 1.0, "DDS", "051"),
                                               makeCard(2.5, 3.5, "DDS", "051")};
    cardscan::consolidateCards(cards, opt);
    check(cards.size() == 2, "the same card after a pause counts twice");
  }
  {
    std::vector<cardscan::CardResult> cards = {makeCard(0.0, 1.0, "DDS", "051"),
                                               makeCard(1.1, 2.0, "C18", "150"),
                                               makeCard(2.1, 3.0, "DDS", "051")};
    cardscan::consolidateCards(cards, opt);
    check(cards.size() == 3, "the same card after another card counts twice");
  }
  {
    std::vector<cardscan::CardResult> cards = {makeCard(0.0, 1.0, "DDS", "051"),
                                               makeCard(1.05, 2.0, "DOS", "051")};
    cardscan::consolidateCards(cards, opt);
    check(cards.size() == 1, "a confusable set-code spelling still merges");
  }
}

void testCountingIgnoresThinSegments() {
  cardscan::AnalyzerOptions opt;
  std::vector<cardscan::ReadSample> reads;
  for (int i = 0; i < 3; ++i) reads.push_back(makeReadSample(i, i * 0.1, "DDS", "051", "065"));
  for (int i = 0; i < 8; ++i) {
    reads.push_back(makeReadSample(40 + i, 4.0 + i * 0.1, "C18", "150", "307"));
  }
  const std::vector<cardscan::CardResult> cards = cardscan::buildCardsFromReads(reads, opt);
  check(cards.size() == 1, "counting drops a segment with too few reads");
  if (!cards.empty()) check(cards[0].identity == "C18:150", "kept segment is the dense one");
}

void testSetSnapFromIndex() {
  const std::string path = "/tmp/cardscan_test_index.tsv";
  {
    std::ofstream out(path);
    out << "TMT\t67\tNinja Teen\t89e9763f76eb3677\tddc0fedc3efcfe7e\n";
    out << "WAR\t149\tRal's Outburst\taabbccddeeff0011\t0011223344556677\n";
  }
  cardscan::HashIndex index;
  check(index.load(path), "a five-column index loads without a reference path");
  check(index.setWithNumber("TMT", "0067", 2.0) == "TMT", "an exact set code stays");
  check(index.setWithNumber("TRTT", "0067", 2.0) == "TMT", "a misread code snaps to the set that has the number");
  check(index.setWithNumber("WAH", "149", 2.0) == "WAR", "one-letter misreads snap");
  check(index.setWithNumber("DDS", "999", 2.0).empty(), "an unknown number stays unresolved");
  check(index.setWithNumber("XYZ", "0067", 2.0).empty(), "a far set code is not guessed");
  check(index.lookupInSet("89e9763f76eb3677", "ddc0fedc3efcfe7e", "TMT", 2).size() == 1,
        "a lookup inside one set finds only that set");
  check(index.lookupInSet("89e9763f76eb3677", "ddc0fedc3efcfe7e", "WAR", 2).size() == 1,
        "a lookup inside another set is not empty: it ranks that set's prints");
  check(index.lookupInSet("89e9763f76eb3677", "ddc0fedc3efcfe7e", "", 2).empty(),
        "a set-filtered lookup without a set returns nothing");
  std::remove(path.c_str());
}

cv::Mat makeTexturedCard(uint64_t seed) {
  cv::Mat card(488, 352, CV_8UC3, cv::Scalar(24, 24, 24));
  cv::RNG rng(seed);
  for (int i = 0; i < 80; ++i) {
    const cv::Point2f center(rng.uniform(10.f, 342.f), rng.uniform(10.f, 478.f));
    const float radius = rng.uniform(3.f, 14.f);
    cv::circle(card, center, radius,
               cv::Scalar(rng.uniform(0, 255), rng.uniform(0, 255), rng.uniform(0, 255)), -1);
  }
  return card;
}

cardscan::IndexMatch firstByName(const cardscan::HashIndex &index, const std::string &name) {
  const auto found = index.findByName(name, 1);
  return found.empty() ? cardscan::IndexMatch{} : found.front();
}

void testOrbSidecar() {
  // A textured card-like image: ORB needs local structure, so scatter deterministic
  // shapes across the whole face (the art region is cropped out of the middle).
  const cv::Mat card = makeTexturedCard(42);

  const std::string image = "/tmp/cardscan_test_orb_card.png";
  const std::string meta = "/tmp/cardscan_test_orb_meta.jsonl";
  const std::string tsv = "/tmp/cardscan_test_orb_index.tsv";
  check(cv::imwrite(image, card), "write the synthetic card image");
  {
    std::ofstream out(meta);
    out << "{\"set\":\"TST\",\"collector\":\"001\",\"name\":\"Orb Probe\",\"id\":\"a\","
        << "\"path\":\"" << image << "\"}\n";
    out << "{\"set\":\"TST\",\"collector\":\"001\",\"name\":\"Orb Probe\",\"id\":\"b\","
        << "\"path\":\"" << image << "\"}\n";
  }
  check(cardscan::buildHashIndex(meta, tsv), "buildHashIndex writes the index");
  {
    std::ifstream orb(tsv + ".orb");
    check(orb.good(), "buildHashIndex writes the .orb sidecar");
  }

  cardscan::HashIndex viaOrb;
  check(viaOrb.load(tsv) && viaOrb.size() == 1,
        "the index with a sidecar loads, one line per cached image");
  const std::vector<cardscan::IndexMatch> found = viaOrb.lookupCard(card, 1);
  check(found.size() == 1 && found[0].setCode == "TST", "lookupCard finds the indexed card");
  if (found.empty()) {
    std::remove(image.c_str());
    std::remove(meta.c_str());
    std::remove(tsv.c_str());
    std::remove((tsv + ".orb").c_str());
    std::remove((tsv + ".lock").c_str());
    return;
  }
  const cardscan::ArtVerification withOrb = viaOrb.verifyArt(card, found[0]);
  check(withOrb.goodMatches >= 50, "verifyArt via .orb matches the artwork");
  check(withOrb.inliers >= 20, "verifyArt via .orb finds homography inliers");

  // The point of the sidecar: the reference image is gone, verification must still work.
  std::remove(image.c_str());
  cardscan::HashIndex noImages;
  check(noImages.load(tsv) && noImages.size() == 1, "the index loads without the image");
  const cardscan::ArtVerification withoutJpg = noImages.verifyArt(card, found[0]);
  check(withoutJpg.goodMatches == withOrb.goodMatches && withoutJpg.inliers == withOrb.inliers,
        "verifyArt works with only the .orb sidecar");

  // A re-exported TSV must not silently reuse the stale sidecar: the checksum mismatch
  // downgrades to the image path, and with the image gone there is no reference at all.
  std::string original;
  {
    std::ifstream t(tsv, std::ios::binary);
    std::ostringstream buffer;
    buffer << t.rdbuf();
    original = buffer.str();
  }
  {
    std::ofstream t(tsv, std::ios::binary | std::ios::trunc);
    t << ("X" + original.substr(1));  // same line count, different bytes
  }
  cardscan::HashIndex reexported;
  check(reexported.load(tsv) && reexported.size() == 1, "a rebuilt TSV still loads");
  const cardscan::ArtVerification rejected = reexported.verifyArt(card, found[0]);
  check(rejected.goodMatches == 0 && rejected.inliers == 0,
        "a TSV that differs from the sidecar build rejects the stale .orb");
  {
    std::ofstream t(tsv, std::ios::binary | std::ios::trunc);
    t << original;
  }

  // Without the sidecar the reference is recomputed from the image; same input, same result.
  std::remove((tsv + ".orb").c_str());
  check(cv::imwrite(image, card), "restore the card image for the fallback path");
  cardscan::HashIndex viaImages;
  check(viaImages.load(tsv) && viaImages.size() == 1, "the index without a sidecar loads");
  const cardscan::ArtVerification withImages = viaImages.verifyArt(card, found[0]);
  check(withImages.goodMatches == withOrb.goodMatches && withImages.inliers == withOrb.inliers,
        "verifyArt via imread fallback gives the same numbers");

  std::remove(image.c_str());
  std::remove(meta.c_str());
  std::remove(tsv.c_str());
  std::remove((tsv + ".orb").c_str());
  std::remove((tsv + ".lock").c_str());
}

void testOrbMerge() {
  const cv::Mat cardA = makeTexturedCard(7);
  const cv::Mat cardB = makeTexturedCard(1234);
  const cv::Mat cardA2 = makeTexturedCard(99);
  const std::string imageA = "/tmp/cardscan_test_merge_a.png";
  const std::string imageB = "/tmp/cardscan_test_merge_b.png";
  const std::string meta = "/tmp/cardscan_test_merge_meta.jsonl";
  const std::string tsv = "/tmp/cardscan_test_merge_index.tsv";

  auto writeMeta = [&](const std::vector<std::string> &images) {
    std::ofstream out(meta);
    for (size_t i = 0; i < images.size(); ++i) {
      out << "{\"set\":\"M" << i << "\",\"collector\":\"00" << (i + 1)
          << "\",\"name\":\"Merge " << i << "\",\"path\":\"" << images[i] << "\"}\n";
    }
  };

  auto writeMetaNamed = [&](const std::vector<std::pair<std::string, std::string>> &entries) {
    std::ofstream out(meta);
    for (size_t i = 0; i < entries.size(); ++i) {
      out << "{\"set\":\"M" << i << "\",\"collector\":\"00" << (i + 1)
          << "\",\"name\":\"" << entries[i].second << "\",\"path\":\"" << entries[i].first
          << "\"}\n";
    }
  };

  check(cv::imwrite(imageA, cardA), "write card A");
  writeMeta({imageA});
  check(cardscan::buildHashIndex(meta, tsv), "initial build");
  std::remove(imageA.c_str());  // the steady state: jpgs live only until the build

  // A new print arrives; A has no jpg anymore, its record must be reused as is.
  check(cv::imwrite(imageB, cardB), "write card B");
  writeMeta({imageA, imageB});
  check(cardscan::buildHashIndex(meta, tsv), "merge build picks up card B");
  cardscan::HashIndex merged;
  check(merged.load(tsv) && merged.size() == 2, "merged index holds both cards");
  const cardscan::IndexMatch rowA = firstByName(merged, "Merge 0");
  const cardscan::IndexMatch rowB = firstByName(merged, "Merge 1");
  check(!rowA.path.empty() && !rowB.path.empty(), "both merged cards are findable by name");

  std::remove(imageB.c_str());
  const cardscan::ArtVerification verifyA = merged.verifyArt(cardA, rowA);
  const cardscan::ArtVerification verifyB = merged.verifyArt(cardB, rowB);
  check(verifyA.goodMatches >= 50 && verifyA.inliers >= 20,
        "card A verifies from its reused record without the jpg");
  check(verifyB.goodMatches >= 50 && verifyB.inliers >= 20,
        "card B verifies from its fresh record without the jpg");

  // No meta change and no jpgs: a rebuild must keep the published pair usable.
  check(cardscan::buildHashIndex(meta, tsv), "no-op rebuild succeeds");
  cardscan::HashIndex unchanged;
  check(unchanged.load(tsv) && unchanged.size() == 2, "no-op rebuild keeps the index");
  check(unchanged.verifyArt(cardA, firstByName(unchanged, "Merge 0")).goodMatches ==
            verifyA.goodMatches,
        "no-op rebuild keeps card A's record");

  // A re-downloaded image is newer than the sidecar: its record must be recomputed.
  check(cv::imwrite(imageA, cardA2), "rewrite card A with new artwork");
  check(cardscan::buildHashIndex(meta, tsv), "rebuild after the image changed");
  cardscan::HashIndex updated;
  check(updated.load(tsv) && updated.size() == 2, "rebuild after change loads");
  const cardscan::IndexMatch changedRow = firstByName(updated, "Merge 0");
  const cardscan::ArtVerification verifyNew = updated.verifyArt(cardA2, changedRow);
  const cardscan::ArtVerification verifyOld = updated.verifyArt(cardA, changedRow);
  check(verifyNew.goodMatches >= 50, "the changed artwork verifies against its new record");
  check(verifyOld.goodMatches * 3 < verifyNew.goodMatches,
        "the old artwork no longer matches the recomputed record");

  // A recomputed row carries the fields of the meta log, not of the old TSV line.
  check(cv::imwrite(imageB, cardB), "rewrite card B for the rename pass");
  writeMetaNamed({{imageA, "Merge 0"}, {imageB, "Merge One"}});
  check(cardscan::buildHashIndex(meta, tsv), "rebuild after the meta name changed");
  cardscan::HashIndex renamed;
  check(renamed.load(tsv) && renamed.size() == 2, "rebuild after rename loads");
  check(!firstByName(renamed, "Merge One").path.empty(),
        "the recomputed row carries the new meta name");
  check(firstByName(renamed, "Merge 1").path.empty(), "the old TSV name is gone");

  std::remove(imageA.c_str());
  std::remove(imageB.c_str());
  std::remove(meta.c_str());
  std::remove(tsv.c_str());
  std::remove((tsv + ".orb").c_str());
  std::remove((tsv + ".lock").c_str());
}

}  // namespace

int main() {
  testOrderQuad();
  testGeometry();
  testParsing();
  testHashHelpers();
  testTracker();
  testTrackerGap();
  testVoteDigits();
  testMergeReads();
  testSnapSetCode();
  testCountingFromReads();
  testCountingIgnoresThinSegments();
  testDuplicateCards();
  testSetSnapFromIndex();
  testOrbSidecar();
  testOrbMerge();
  std::printf("%d checks, %d failures\n", gChecks, gFailures);
  return gFailures == 0 ? 0 : 1;
}
