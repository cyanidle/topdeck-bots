#include "analyzer.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <opencv2/img_hash.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>

#include "overlay.h"
#include "phash.h"
#include "tracker.h"
#include "evidence.h"
#include "text_runs.h"

namespace cardscan {
namespace {

constexpr double kCardAspect = 63.0 / 88.0;

std::string hashCrop(const cv::Mat &crop) { return perceptualHash(crop); }

double qualityOf(const cv::Mat &crop, const Detection &d, cv::Size frameSize) {
  if (crop.empty()) return 0.0;
  cv::Mat gray;
  cv::cvtColor(crop, gray, cv::COLOR_BGR2GRAY);
  cv::Mat lap;
  cv::Laplacian(gray, lap, CV_64F);
  cv::Scalar mean, stddev;
  cv::meanStdDev(lap, mean, stddev);
  const double sharp = std::min(1.0, stddev[0] * stddev[0] / 300.0);
  const double frameArea = static_cast<double>(frameSize.width) * frameSize.height;
  const double sizeScore = std::min(1.0, d.area / (0.12 * frameArea));
  const double aspect = 1.0 - std::min(1.0, std::abs(d.aspect - kCardAspect) / 0.30);
  return 0.45 * sharp + 0.35 * sizeScore + 0.20 * aspect;
}

struct VerifiedCandidate {
  Detection detection;
  cv::Mat crop;
  double quality = 0.0;
  CardEvidence evidence;
  SerialEvidence serial;
  int textRuns = 0;
  double score = 0.0;
  std::string hash;
};

double structuralOf(const CardEvidence &evidence) {
  return std::max(evidence.borderContrast, evidence.topBarContrast);
}

Quad expandQuadSides(const Quad &q, double dx, double dyTop, double dyBottom) {
  const double offsets[4] = {dyTop, dx, dyBottom, dx};
  cv::Point2f lineA[4], lineB[4];
  for (int i = 0; i < 4; ++i) {
    const cv::Point2f a = q[i];
    const cv::Point2f b = q[(i + 1) % 4];
    const double len = cv::norm(b - a);
    if (len < 1.0) return q;
    const cv::Point2f d(static_cast<float>((b.x - a.x) / len), static_cast<float>((b.y - a.y) / len));
    const cv::Point2f n(d.y, -d.x);
    lineA[i] = a + n * static_cast<float>(offsets[i]);
    lineB[i] = b + n * static_cast<float>(offsets[i]);
  }
  Quad out;
  for (int i = 0; i < 4; ++i) {
    const int prev = (i + 3) % 4;
    const double x1 = lineA[prev].x, y1 = lineA[prev].y, x2 = lineB[prev].x, y2 = lineB[prev].y;
    const double x3 = lineA[i].x, y3 = lineA[i].y, x4 = lineB[i].x, y4 = lineB[i].y;
    const double denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4);
    if (std::abs(denom) < 1e-6) return q;
    const double t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom;
    out[i] = cv::Point2f(static_cast<float>(x1 + t * (x2 - x1)),
                         static_cast<float>(y1 + t * (y2 - y1)));
  }
  return out;
}

std::vector<VerifiedCandidate> verifyCandidates(const cv::Mat &frame,
                                                const std::vector<Detection> &detections,
                                                const AnalyzerOptions &opt) {
  const CardRegions regions = defaultRegions(opt.ocr);
  std::vector<VerifiedCandidate> candidates;
  for (const auto &d : detections) {
    if (!allCornersInside(d.quad, frame.size(), 2.f)) continue;
    VerifiedCandidate v;
    v.detection = d;
    const Quad cropQuad = expandQuadSides(d.quad, 0.015 * quadWidth(d.quad),
                                          0.015 * quadHeight(d.quad),
                                          opt.cropBottomExpansion * quadHeight(d.quad));
    v.crop = warpQuad(frame, cropQuad, opt.ocr.cardWidth, opt.ocr.cardHeight);
    v.quality = qualityOf(v.crop, d, frame.size());
    v.evidence = analyzeCard(v.crop);
    v.serial = analyzeSerialLine(v.crop);
    v.hash = hashCrop(v.crop);
    v.textRuns = countTextRuns(v.crop, regions.collectorBlock);
    const double structural = structuralOf(v.evidence);
    const double structuralBoost = 1.0 + 2.0 * std::min(1.5, structural * 8.0);
    const double textBoost = 1.0 + 0.5 * std::min(1.0, v.textRuns / 8.0);
    const double serialBoost = v.serial.accepted ? 1.0 + 2.0 * v.serial.score : 0.25;
    v.score = d.score * structuralBoost * textBoost * serialBoost;
    candidates.push_back(std::move(v));
  }
  std::sort(candidates.begin(), candidates.end(),
            [](const VerifiedCandidate &a, const VerifiedCandidate &b) { return a.score > b.score; });

  std::vector<VerifiedCandidate> kept;
  for (auto &c : candidates) {
    bool drop = false;
    for (const auto &k : kept) {
      if (quadIoU(c.detection.quad, k.detection.quad) > 0.70 ||
          quadContainment(c.detection.quad, k.detection.quad) > 0.90) {
        drop = true;
        break;
      }
    }
    if (!drop) kept.push_back(std::move(c));
  }
  return kept;
}

bool looksLikeCard(const VerifiedCandidate &v, const AnalyzerOptions &opt) {
  if (v.serial.accepted) return true;
  return structuralOf(v.evidence) >= opt.minStructuralContrast && v.textRuns >= opt.strongTextRuns;
}

const TrackRead *bestIdentityRead(const Track &t) {
  const TrackRead *best = nullptr;
  for (const auto &r : t.reads) {
    if (r.read.identityKey().empty()) continue;
    if (!best || r.read.score > best->read.score ||
        (r.read.score == best->read.score && r.quality > best->quality)) {
      best = &r;
    }
  }
  return best;
}

bool hasStrongIdentity(const Track &t) {
  const TrackRead *r = bestIdentityRead(t);
  return r && r->read.score >= 10;
}

std::string currentLabel(const Track &t) {
  const TrackRead *r = bestIdentityRead(t);
  if (!r) return "?";
  return r->read.display();
}

void fillFromRead(CardResult &c, const OcrRead &r) {
  c.setCode = r.setCode;
  c.number = r.number;
  c.total = r.total;
  c.language = r.language;
  c.rarity = r.rarity;
  if (!r.name.empty()) c.name = r.name;
}

CardResult resolveTrack(const Track &t, const AnalyzerOptions &) {
  CardResult c;
  c.trackId = t.id;
  c.hits = t.hits;
  c.firstFrame = t.firstFrame;
  c.lastFrame = t.lastFrame;
  c.firstTs = t.firstTs;
  c.lastTs = t.lastTs;
  c.bestFrame = t.bestFrame;
  c.bestTs = t.bestTs;
  c.quality = t.bestQuality;
  c.quad.assign(t.bestQuad.begin(), t.bestQuad.end());
  c.hash = t.bestHash;
  c.mergedTrackIds.push_back(t.id);

  std::map<std::string, std::pair<int, double>> votes;
  std::map<std::string, const TrackRead *> bestPerKey;
  int readsWithIdentity = 0;
  for (const auto &r : t.reads) {
    const std::string key = r.read.identityKey();
    if (key.empty()) continue;
    ++readsWithIdentity;
    auto &v = votes[key];
    v.first += 1;
    v.second += r.read.score * (0.5 + 0.5 * r.quality);
    const TrackRead *&prev = bestPerKey[key];
    if (!prev || r.read.score > prev->read.score ||
        (r.read.score == prev->read.score && r.quality > prev->quality)) {
      prev = &r;
    }
  }

  std::string bestKey;
  int bestCount = 0;
  double bestWeight = 0.0;
  for (const auto &kv : votes) {
    if (kv.second.first > bestCount ||
        (kv.second.first == bestCount && kv.second.second > bestWeight)) {
      bestKey = kv.first;
      bestCount = kv.second.first;
      bestWeight = kv.second.second;
    }
  }

  if (!bestKey.empty()) {
    const TrackRead *chosen = bestPerKey[bestKey];
    const bool enough = bestCount >= 2 || (chosen && chosen->read.score >= 10);
    if (enough && chosen) {
      fillFromRead(c, chosen->read);
      c.identified = true;
      c.identifiedByCodes = true;
      c.identity = bestKey;
      const double agreement = readsWithIdentity > 0
                                   ? static_cast<double>(bestCount) / readsWithIdentity
                                   : 0.0;
      c.confidence = std::min(1.0, (chosen->read.score / 11.0) * (0.4 + 0.6 * agreement));
    }
  }

  if (!c.identified) {
    const TrackRead *best = nullptr;
    for (const auto &r : t.reads) {
      if (!best || r.read.score > best->read.score) best = &r;
    }
    if (best) fillFromRead(c, best->read);
  }

  if (!t.title.empty() && c.name.empty()) c.name = t.title;
  if (!c.identified && !c.name.empty() && c.name.size() >= 4) {
    std::string key = "N:";
    for (char ch : c.name) key.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(ch))));
    c.identity = key;
    c.identified = true;
    c.identifiedByCodes = false;
    c.confidence = std::max(0.3, c.confidence);
  }
  if (!c.identified) c.identity.clear();
  return c;
}

void mergeInto(CardResult &base, const CardResult &other) {
  const bool otherBetter = other.confidence > base.confidence;
  base.firstFrame = std::min(base.firstFrame, other.firstFrame);
  base.firstTs = std::min(base.firstTs, other.firstTs);
  base.lastFrame = std::max(base.lastFrame, other.lastFrame);
  base.lastTs = std::max(base.lastTs, other.lastTs);
  base.hits += other.hits;
  if (otherBetter) {
    const int keepFirstFrame = base.firstFrame;
    const double keepFirstTs = base.firstTs;
    const int keepLastFrame = base.lastFrame;
    const double keepLastTs = base.lastTs;
    const int keepHits = base.hits;
    std::vector<int> ids = base.mergedTrackIds;
    base = other;
    base.firstFrame = keepFirstFrame;
    base.firstTs = keepFirstTs;
    base.lastFrame = keepLastFrame;
    base.lastTs = keepLastTs;
    base.hits = keepHits;
    base.mergedTrackIds = ids;
  }
  for (int id : other.mergedTrackIds) base.mergedTrackIds.push_back(id);
}

std::vector<CardResult> buildCards(std::vector<Track> &tracks, CardOcr *ocr,
                                   const AnalyzerOptions &opt) {
  if (ocr && ocr->available() && opt.enableTitleFallback) {
    for (auto &t : tracks) {
      if (!t.confirmed(opt.minTrackHits)) continue;
      if (bestIdentityRead(t)) continue;
      if (t.titleAttempts > 0 || t.bestCrop.empty()) continue;
      t.titleAttempts = 1;
      t.title = ocr->readTitle(t.bestCrop);
    }
  }

  std::vector<CardResult> merged;
  std::map<std::string, size_t> byIdentity;
  std::vector<CardResult> unidentified;

  for (auto &t : tracks) {
    if (!t.confirmed(opt.minTrackHits)) continue;
    if (t.bestTextRuns < opt.minTextRuns && t.bestStructural < opt.minStructuralContrast) continue;
    CardResult c = resolveTrack(t, opt);
    if (c.identity.empty()) {
      unidentified.push_back(c);
      continue;
    }
    auto it = byIdentity.find(c.identity);
    if (it == byIdentity.end()) {
      byIdentity[c.identity] = merged.size();
      merged.push_back(c);
    } else {
      mergeInto(merged[it->second], c);
    }
  }

  std::vector<CardResult> unid;
  for (const auto &c : unidentified) {
    bool folded = false;
    for (auto &u : unid) {
      if (hashDistance(c.hash, u.hash) <= opt.hashMergeDistance &&
          (c.lastTs + opt.hashMergeGapSeconds < u.firstTs ||
           u.lastTs + opt.hashMergeGapSeconds < c.firstTs)) {
        mergeInto(u, c);
        folded = true;
        break;
      }
    }
    if (!folded) unid.push_back(c);
  }
  merged.insert(merged.end(), unid.begin(), unid.end());

  std::sort(merged.begin(), merged.end(), [](const CardResult &a, const CardResult &b) {
    if (a.firstFrame != b.firstFrame) return a.firstFrame < b.firstFrame;
    return a.trackId < b.trackId;
  });
  return merged;
}

void drawTrack(cv::Mat &canvas, const Quad &quad, const Track &t, const AnalyzerOptions &opt,
               const OverlayStyle &style) {
  const bool identified = hasStrongIdentity(t);
  const bool confirmed = t.confirmed(opt.minTrackHits);
  const cv::Scalar color = identified ? style.identified : (confirmed ? style.unidentified : style.pending);
  char buf[256];
  std::snprintf(buf, sizeof(buf), "T%d %s q=%.2f h=%d", t.id, currentLabel(t).c_str(),
                t.bestQuality, t.hits);
  drawQuad(canvas, quad, color, buf, style);
}

int numericValue(const std::string &s) {
  try {
    return std::stoi(s);
  } catch (...) {
    return -1;
  }
}

}  // namespace

void consolidateCards(std::vector<CardResult> &cards) {
  struct Cluster {
    CardResult card;
    std::map<std::string, double> codeVotes;
  };
  std::vector<Cluster> clusters;
  for (auto &card : cards) {
    if (!card.identifiedByCodes) {
      Cluster c;
      c.card = card;
      clusters.push_back(c);
      continue;
    }
    Cluster *target = nullptr;
    for (auto &candidate : clusters) {
      if (!candidate.card.identifiedByCodes) continue;
      const int na = numericValue(card.number);
      const int nb = numericValue(candidate.card.number);
      if (na <= 0 || nb <= 0 || na != nb) continue;
      if (codeDistance(card.setCode, candidate.card.setCode) > 2.5) continue;
      target = &candidate;
      break;
    }
    if (!target) {
      Cluster c;
      c.card = card;
      c.codeVotes[card.setCode] += card.confidence * card.hits;
      clusters.push_back(c);
      continue;
    }
    target->codeVotes[card.setCode] += card.confidence * card.hits;
    CardResult &into = target->card;
    const bool better =
        card.confidence * card.hits > into.confidence * into.hits;
    into.hits += card.hits;
    into.firstFrame = std::min(into.firstFrame, card.firstFrame);
    into.firstTs = std::min(into.firstTs, card.firstTs);
    into.lastFrame = std::max(into.lastFrame, card.lastFrame);
    into.lastTs = std::max(into.lastTs, card.lastTs);
    for (int id : card.mergedTrackIds) into.mergedTrackIds.push_back(id);
    if (better) {
      into.setCode = card.setCode;
      into.confidence = card.confidence;
      into.quality = card.quality;
      into.bestFrame = card.bestFrame;
      into.bestTs = card.bestTs;
      into.quad = card.quad;
      into.hash = card.hash;
      into.total = card.total;
      into.rarity = card.rarity;
      into.language = card.language;
    }
  }

  std::vector<CardResult> merged;
  merged.reserve(clusters.size());
  for (auto &cluster : clusters) {
    if (cluster.card.identifiedByCodes && !cluster.codeVotes.empty()) {
      std::string bestCode;
      double bestWeight = -1.0;
      for (const auto &vote : cluster.codeVotes) {
        if (vote.second > bestWeight) {
          bestWeight = vote.second;
          bestCode = vote.first;
        }
      }
      if (!bestCode.empty()) {
        cluster.card.setCode = bestCode;
        cluster.card.identity = bestCode + ":" + cluster.card.number;
      }
    }
    merged.push_back(cluster.card);
  }
  std::sort(merged.begin(), merged.end(), [](const CardResult &a, const CardResult &b) {
    if (a.firstFrame != b.firstFrame) return a.firstFrame < b.firstFrame;
    return a.trackId < b.trackId;
  });
  cards.swap(merged);
}

std::string voteDigits(const std::vector<std::string> &values) {
  if (values.empty()) return {};
  std::map<size_t, int> lengthVotes;
  for (const auto &value : values) {
    if (!value.empty()) lengthVotes[value.size()] += 1;
  }
  if (lengthVotes.empty()) return {};
  size_t length = 0;
  int bestCount = -1;
  for (const auto &vote : lengthVotes) {
    if (vote.second > bestCount) {
      bestCount = vote.second;
      length = vote.first;
    }
  }
  if (length == 0 || length > 6) return {};
  std::vector<std::map<char, double>> votes(length);
  int used = 0;
  for (const auto &value : values) {
    if (value.size() != length) continue;
    ++used;
    for (size_t i = 0; i < length; ++i) votes[i][value[i]] += 1.0;
  }
  if (used == 0) return {};
  std::string out;
  for (const auto &position : votes) {
    char best = '0';
    double bestWeight = -1.0;
    for (const auto &entry : position) {
      if (entry.second > bestWeight) {
        bestWeight = entry.second;
        best = entry.first;
      }
    }
    out.push_back(best);
  }
  return out;
}

std::vector<CardResult> buildCardsFromReads(const std::vector<ReadSample> &reads,
                                            const AnalyzerOptions &opt) {
  std::vector<const ReadSample *> samples;
  for (const auto &r : reads) samples.push_back(&r);
  std::sort(samples.begin(), samples.end(),
            [](const ReadSample *a, const ReadSample *b) { return a->ts < b->ts; });

  struct Segment {
    std::vector<const ReadSample *> items;
    std::map<std::string, double> codeVotes;
    int incompatibleRun = 0;
  };
  std::vector<Segment> segments;

  auto majorityCode = [](const Segment &segment) {
    std::string best;
    double bestWeight = -1.0;
    for (const auto &vote : segment.codeVotes) {
      if (vote.second > bestWeight) {
        bestWeight = vote.second;
        best = vote.first;
      }
    }
    return best;
  };

  for (const auto *sample : samples) {
    bool startNew = segments.empty();
    if (!startNew) {
      Segment &current = segments.back();
      const double gap = sample->ts - current.items.back()->ts;
      if (gap > opt.segmentGapSeconds ||
          sample->ts - current.items.front()->ts > opt.maxSegmentSeconds) {
        startNew = true;
      } else {
        const std::string majority = majorityCode(current);
        const bool incompatible =
            !majority.empty() && !sample->code.empty() &&
            codeDistance(majority, sample->code) > opt.segmentCodeDistance;
        if (incompatible) {
          ++current.incompatibleRun;
          if (current.incompatibleRun >= opt.segmentIncompatibleRun) startNew = true;
        } else {
          current.incompatibleRun = 0;
        }
      }
    }
    if (startNew) {
      Segment segment;
      segment.items.push_back(sample);
      if (!sample->code.empty()) segment.codeVotes[sample->code] += 0.2 + sample->quality;
      segments.push_back(std::move(segment));
    } else {
      Segment &current = segments.back();
      current.items.push_back(sample);
      if (!sample->code.empty()) current.codeVotes[sample->code] += 0.2 + sample->quality;
    }
  }

  std::vector<CardResult> cards;
  int nextId = 1;
  for (const auto &segment : segments) {
    if (static_cast<int>(segment.items.size()) < std::max(2, opt.minSegmentReads)) continue;
    std::vector<std::string> numbers, totals;
    std::map<std::string, double> codeVotes;
    const ReadSample *chosen = nullptr;
    for (const auto *item : segment.items) {
      if (!item->number.empty()) numbers.push_back(item->number);
      if (!item->total.empty()) totals.push_back(item->total);
      if (!item->code.empty()) codeVotes[item->code] += 0.2 + item->quality;
      if (!chosen || item->quality > chosen->quality) chosen = item;
    }
    std::string code;
    double codeWeight = -1.0;
    for (const auto &vote : codeVotes) {
      if (vote.second > codeWeight) {
        codeWeight = vote.second;
        code = vote.first;
      }
    }

    CardResult card;
    card.trackId = nextId++;
    card.setCode = code;
    card.number = voteDigits(numbers);
    card.total = voteDigits(totals);
    card.language = chosen ? chosen->language : "";
    card.rarity = chosen ? chosen->rarity : "";
    card.name = chosen ? chosen->name : "";
    if (!code.empty() && !card.number.empty()) {
      card.identity = code + ":" + card.number;
      card.identified = true;
      card.identifiedByCodes = true;
    }
    card.confidence = chosen ? chosen->quality : 0.0;
    card.quality = chosen ? chosen->quality : 0.0;
    card.hits = static_cast<int>(segment.items.size());
    card.firstFrame = segment.items.front()->frame;
    card.firstTs = segment.items.front()->ts;
    card.lastFrame = segment.items.back()->frame;
    card.lastTs = segment.items.back()->ts;
    card.bestFrame = chosen ? chosen->frame : card.firstFrame;
    card.bestTs = chosen ? chosen->ts : card.firstTs;
    if (chosen) {
      card.quad = chosen->quad;
      card.hash = chosen->hash;
    }
    card.mergedTrackIds.push_back(card.trackId);
    cards.push_back(card);
  }
  return cards;
}

bool hasImageExtension(const std::string &path) {
  const size_t dot = path.find_last_of('.');
  if (dot == std::string::npos) return false;
  std::string ext = path.substr(dot + 1);
  std::transform(ext.begin(), ext.end(), ext.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  static const char *kImages[] = {"jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"};
  for (const char *e : kImages) {
    if (ext == e) return true;
  }
  return false;
}

Analyzer::Analyzer(const AnalyzerOptions &options) : opt_(options) {}

ImageResult Analyzer::analyzeImage(const std::string &path) {
  cv::Mat image = cv::imread(path, cv::IMREAD_COLOR);
  if (image.empty()) throw std::runtime_error("cannot read image: " + path);

  ImageResult result;
  result.input = path;
  result.width = image.cols;
  result.height = image.rows;

  CardDetector detector(opt_.detector);
  cv::Mat edges;
  std::vector<Quad> rawQuads;
  const auto detections = [&]() {
    if (opt_.debugDir.empty()) return detector.detect(image);
    return detector.detect(image, &edges, &rawQuads);
  }();

  if (!opt_.debugDir.empty()) {
    std::filesystem::create_directories(opt_.debugDir);
    cv::imwrite(opt_.debugDir + "/edges.png", edges);
    cv::Mat canvas = image.clone();
    OverlayStyle style;
    for (const auto &q : rawQuads) drawQuad(canvas, q, cv::Scalar(255, 0, 255), "", style);
    for (const auto &d : detections) drawQuad(canvas, d.quad, style.identified, "candidate", style);
    cv::imwrite(opt_.debugDir + "/quads.jpg", canvas);
  }

  std::unique_ptr<CardOcr> ocr;
  if (opt_.enableOcr) {
    ocr = std::make_unique<CardOcr>(opt_.ocr);
    if (!ocr->available()) ocr.reset();
  }

  int nextId = 1;
  struct ScoredCandidate {
    VerifiedCandidate candidate;
    OcrRead read;
    double rank = 0.0;
  };
  std::vector<ScoredCandidate> scored;
  for (auto &v : verifyCandidates(image, detections, opt_)) {
    if (opt_.verbose) {
      const Detection &d = v.detection;
      std::fprintf(stderr,
                   "[cand %d] score=%.3f geo=%.3f support=%.2f src=%s runs=%d serial=%d(%.2f,%d) "
                   "edge=%.3f border=%.3f frame=%.3f\n",
                   nextId, v.score, d.score, d.support,
                   d.source == DetectionSource::Lines ? "lines" : "contour", v.textRuns,
                   v.serial.glyphs, v.serial.backgroundMean, static_cast<int>(v.serial.accepted),
                   v.evidence.edgeDensity, v.evidence.borderContrast, v.evidence.topBarContrast);
    }
    ScoredCandidate s;
    s.candidate = std::move(v);
    if (ocr && ocr->available()) {
      if (!opt_.debugDir.empty()) {
        char prefix[32];
        std::snprintf(prefix, sizeof(prefix), "cand_%02d", nextId);
        ocr->setDebugDir(opt_.debugDir, prefix);
      }
      s.read = ocr->readCard(s.candidate.crop);
    }
    double rank = s.candidate.score;
    if (s.read.hasIdentity()) {
      rank += 100.0 + s.read.score;
    } else if (s.read.hasCollector()) {
      rank += 50.0 + s.read.score;
    } else if (s.read.hasSetCode()) {
      rank += 20.0 + s.read.score;
    }
    s.rank = rank;
    scored.push_back(std::move(s));
    ++nextId;
  }
  std::sort(scored.begin(), scored.end(),
            [](const ScoredCandidate &a, const ScoredCandidate &b) { return a.rank > b.rank; });

  nextId = 1;
  std::vector<Quad> accepted;
  for (auto &s : scored) {
    const VerifiedCandidate &v = s.candidate;
    const bool evidence = s.read.hasIdentity() || s.read.hasCollector() || looksLikeCard(v, opt_);
    if (!evidence) continue;
    bool nested = false;
    for (const auto &q : accepted) {
      if (quadIoU(v.detection.quad, q) > opt_.nestedIou ||
          quadContainment(v.detection.quad, q) > opt_.nestedContainment) {
        nested = true;
        break;
      }
    }
    if (nested) continue;
    accepted.push_back(v.detection.quad);

    const cv::Mat &crop = v.crop;
    CardResult c;
    c.trackId = nextId++;
    c.hits = 1;
    c.firstFrame = c.bestFrame = c.lastFrame = 0;
    c.firstTs = c.bestTs = c.lastTs = 0.0;
    c.quality = v.quality;
    c.quad.assign(v.detection.quad.begin(), v.detection.quad.end());
    c.hash = hashCrop(crop);
    fillFromRead(c, s.read);
    if (!s.read.identityKey().empty()) {
      c.identified = true;
      c.identifiedByCodes = true;
      c.identity = s.read.identityKey();
      c.confidence = std::min(1.0, s.read.score / 11.0);
    } else if (ocr && ocr->available() && opt_.enableTitleFallback) {
      c.name = ocr->readTitle(crop);
      if (c.name.size() >= 4) {
        c.identity = "N:" + asciiUpper(c.name);
        c.identified = true;
        c.confidence = 0.3;
      }
    }
    result.cards.push_back(c);
    if (!opt_.debugDir.empty()) {
      char name[64];
      std::snprintf(name, sizeof(name), "/det_%02d_card.jpg", c.trackId);
      cv::imwrite(opt_.debugDir + name, crop);
    }
  }
  return result;
}

VideoResult Analyzer::analyzeVideo(const std::string &path) {
  cv::VideoCapture capture(path);
  if (!capture.isOpened()) throw std::runtime_error("cannot open video: " + path);

  const auto started = std::chrono::steady_clock::now();

  VideoResult result;
  result.input = path;
  result.fps = capture.get(cv::CAP_PROP_FPS);
  if (!(result.fps > 0.1) || result.fps > 240.0) result.fps = 30.0;
  result.frames = static_cast<int>(capture.get(cv::CAP_PROP_FRAME_COUNT));
  result.width = static_cast<int>(capture.get(cv::CAP_PROP_FRAME_WIDTH));
  result.height = static_cast<int>(capture.get(cv::CAP_PROP_FRAME_HEIGHT));
  result.duration = result.frames > 0 ? result.frames / result.fps : 0.0;

  if (result.width <= 0 || result.height <= 0) {
    cv::Mat probe;
    if (capture.read(probe)) {
      result.width = probe.cols;
      result.height = probe.rows;
      capture.set(cv::CAP_PROP_POS_FRAMES, 0);
    }
  }

  CardDetector detector(opt_.detector);
  Tracker tracker;
  std::unique_ptr<CardOcr> ocr;
  if (opt_.enableOcr) {
    ocr = std::make_unique<CardOcr>(opt_.ocr);
    if (!ocr->available()) ocr.reset();
  }

  OverlayStyle style;
  cv::VideoWriter writer;
  if (!opt_.overlayPath.empty()) {
    writer.open(opt_.overlayPath, cv::VideoWriter::fourcc('m', 'p', '4', 'v'), result.fps,
                cv::Size(result.width, result.height));
    if (!writer.isOpened()) throw std::runtime_error("cannot open overlay writer: " + opt_.overlayPath);
  }

  if (!opt_.debugDir.empty()) std::filesystem::create_directories(opt_.debugDir);

  cv::Mat frame;
  int index = 0;
  int lastDebugFrame = -1;
  while (capture.read(frame)) {
    if (opt_.maxFrames > 0 && index >= opt_.maxFrames) break;
    const bool process = opt_.stride <= 1 || index % opt_.stride == 0;
    if (!process) {
      if (writer.isOpened()) writer.write(frame);
      ++index;
      continue;
    }
    ++result.processedFrames;
    const double ts = index / result.fps;

    const auto detections = detector.detect(frame);
    const auto candidates = verifyCandidates(frame, detections, opt_);
    if (opt_.verbose) {
      std::fprintf(stderr, "[frame %d] raw=%zu verified=%zu\n", index, detections.size(),
                   candidates.size());
    }
    std::vector<std::pair<int, const VerifiedCandidate *>> assigned;

    for (const auto &v : candidates) {
      const Detection &d = v.detection;
      int trackId = tracker.match(index, d, v.hash);
      if (trackId < 0) {
        if (!looksLikeCard(v, opt_)) continue;
        trackId = tracker.update(index, ts, d, v.hash, 0);
      } else {
        tracker.update(index, ts, d, v.hash, trackId);
      }
      Track *track = tracker.get(trackId);
      if (!track) continue;
      assigned.emplace_back(trackId, &v);

      const cv::Mat &crop = v.crop;
      if (v.textRuns > track->bestTextRuns) track->bestTextRuns = v.textRuns;
      const double structural = structuralOf(v.evidence);
      if (structural > track->bestStructural) track->bestStructural = structural;
      if (v.quality > track->bestQuality) {
        track->bestQuality = v.quality;
        track->bestHash = hashCrop(crop);
        if (!track->cropDropped) track->bestCrop = crop.clone();
        track->bestQuad = d.quad;
        track->bestFrame = index;
        track->bestTs = ts;
      }

      if (ocr && ocr->available() && opt_.enableOcr) {
        const bool wanted = !hasStrongIdentity(*track) &&
                            track->ocrAttempts < opt_.ocrMaxAttemptsPerTrack &&
                            v.quality >= opt_.ocrMinQuality &&
                            index - track->lastOcrFrame >= opt_.ocrMinFrameGap;
        if (wanted) {
          ++track->ocrAttempts;
          track->lastOcrFrame = index;
          if (!opt_.debugDir.empty()) {
            char prefix[64];
            std::snprintf(prefix, sizeof(prefix), "track_%04d_f%06d", trackId, index);
            ocr->setDebugDir(opt_.debugDir, prefix);
          }
          const OcrRead read = ocr->readCard(crop);
          if (!read.raw.empty()) track->reads.push_back({index, ts, v.quality, read});
        }
      }

      if (!track->cropDropped && !track->bestCrop.empty() && opt_.debugDir.empty() &&
          hasStrongIdentity(*track)) {
        track->bestCrop.release();
        track->cropDropped = true;
      }
    }
    tracker.endFrame(index);

    if (ocr && ocr->available() && opt_.enableOcr && !candidates.empty() && opt_.ocrStride > 0 &&
        index % opt_.ocrStride == 0) {
      const VerifiedCandidate &top = candidates.front();
      if (looksLikeCard(top, opt_)) {
        const OcrRead read = ocr->readCard(top.crop);
        if (!read.raw.empty()) {
          ++result.readsTotal;
          ReadSample sample;
          sample.frame = index;
          sample.ts = ts;
          sample.quality = top.quality;
          sample.code = read.setCode;
          sample.number = read.number;
          sample.total = read.total;
          sample.language = read.language;
          sample.rarity = read.rarity;
          sample.name = read.name;
          sample.identity = read.identityKey();
          sample.hash = top.hash;
          sample.quad.assign(top.detection.quad.begin(), top.detection.quad.end());
          if (!sample.identity.empty()) ++result.readsWithIdentity;
          result.reads.push_back(sample);
        }
      }
    }

    if (writer.isOpened()) {
      cv::Mat canvas = frame.clone();
      for (const auto &entry : assigned) {
        const VerifiedCandidate &v = *entry.second;
        Track *best = tracker.get(entry.first);
        if (!best) continue;
        drawTrack(canvas, v.detection.quad, *best, opt_, style);
      }

      int confirmed = 0, identified = 0;
      for (const auto &t : tracker.tracks()) {
        if (!t.confirmed(opt_.minTrackHits)) continue;
        ++confirmed;
        if (hasStrongIdentity(t)) ++identified;
      }
      char hud[128];
      std::snprintf(hud, sizeof(hud), "frame %d  t=%.2fs  det=%zu  tracks=%zu",
                    index, ts, detections.size(), tracker.tracks().size());
      char hud2[128];
      std::snprintf(hud2, sizeof(hud2), "confirmed=%d  identified=%d  unidentified=%d", confirmed,
                    identified, confirmed - identified);
      drawHud(canvas, {hud, hud2}, style);
      writer.write(canvas);
    }

    if (!opt_.debugDir.empty() && opt_.debugFrameEvery > 0 && !detections.empty() &&
        index - lastDebugFrame >= opt_.debugFrameEvery) {
      lastDebugFrame = index;
      cv::Mat canvas = frame.clone();
      for (const auto &d : detections) {
        std::string label;
        for (const auto &t : tracker.tracks()) {
          if (t.lastFrame == index && quadIoU(t.lastQuad, d.quad) > 0.5) {
            char buf[256];
            std::snprintf(buf, sizeof(buf), "T%d %s q=%.2f h=%d", t.id, currentLabel(t).c_str(),
                          t.bestQuality, t.hits);
            label = buf;
            break;
          }
        }
        drawQuad(canvas, d.quad, style.pending, label, style);
      }
      char name[64];
      std::snprintf(name, sizeof(name), "/frame_%06d.jpg", index);
      cv::imwrite(opt_.debugDir + name, canvas);
    }

    ++index;
  }
  capture.release();

  result.cards = buildCardsFromReads(result.reads, opt_);
  if (result.cards.empty()) result.cards = buildCards(tracker.tracks(), ocr.get(), opt_);
  consolidateCards(result.cards);

  if (!opt_.debugDir.empty() && opt_.saveTrackCrops) {
    for (const auto &t : tracker.tracks()) {
      if (!t.confirmed(opt_.minTrackHits) || t.bestCrop.empty()) continue;
      char name[64];
      std::snprintf(name, sizeof(name), "/track_%04d.jpg", t.id);
      cv::imwrite(opt_.debugDir + name, t.bestCrop);
    }
  }

  if (!opt_.debugDir.empty() && opt_.saveTrackCrops) {
    std::string dump;
    for (const auto &r : result.reads) {
      char line[256];
      std::snprintf(line, sizeof(line), "%.3f f=%d q=%.2f id=%s code=%s num=%s/%s\n", r.ts,
                    r.frame, r.quality, r.identity.c_str(), r.code.c_str(), r.number.c_str(),
                    r.total.c_str());
      dump += line;
    }
    std::ofstream readsOut(opt_.debugDir + "/reads.txt");
    if (readsOut) readsOut << dump;
  }

  result.elapsedSeconds =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
  return result;
}

}  // namespace cardscan
