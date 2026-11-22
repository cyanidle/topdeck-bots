#include "hashindex.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <unordered_set>

#include <opencv2/calib3d.hpp>
#include <opencv2/features2d.hpp>
#include <opencv2/imgcodecs.hpp>

#include "identity.h"
#include "phash.h"

namespace cardscan {
namespace {

// The artwork carries the identity; the frame is nearly identical across a whole set.
cv::Mat artRegion(const cv::Mat &card) {
  if (card.empty()) return {};
  const int x = static_cast<int>(card.cols * 0.10);
  const int y = static_cast<int>(card.rows * 0.11);
  const int w = std::max(8, static_cast<int>(card.cols * 0.80));
  const int h = std::max(8, static_cast<int>(card.rows * 0.55));
  const cv::Rect box = cv::Rect(x, y, w, h) & cv::Rect(0, 0, card.cols, card.rows);
  return card(box).clone();
}

std::string jsonField(const std::string &line, const char *key) {
  const std::string token = std::string("\"") + key + "\":";
  const size_t at = line.find(token);
  if (at == std::string::npos) return {};
  size_t start = at + token.size();
  while (start < line.size() && (line[start] == ' ' || line[start] == '\t')) ++start;
  const bool quoted = start < line.size() && line[start] == '"';
  if (quoted) ++start;
  size_t end = start;
  if (quoted) {
    while (end < line.size() && line[end] != '"') ++end;
  } else {
    while (end < line.size() && line[end] != ',' && line[end] != '}') ++end;
    while (end > start && (line[end - 1] == ' ' || line[end - 1] == '\t')) --end;
  }
  return line.substr(start, end - start);
}

constexpr char kOrbMagic[8] = {'C', 'S', 'O', 'R', 'B', '0', '0', '2'};
constexpr uint32_t kOrbDescriptorBytes = 32;  // one ORB descriptor, fixed by the extractor
constexpr int kOrbKeypoints = 700;            // reference and query extractors must match
constexpr uint64_t kFnvOffsetBasis = 14695981039346656037ULL;

// The .orb records are positionally paired with the TSV lines, so the sidecar is only
// valid for the exact TSV it was built from. FNV-1a over the TSV bytes is the tie-breaker:
// a re-exported TSV (same count, different order) makes the pairing silently wrong, and a
// checksum mismatch downgrades to the imread path instead.
uint64_t fnv1a64(uint64_t hash, const void *data, size_t bytes) {
  const auto *p = static_cast<const unsigned char *>(data);
  for (size_t i = 0; i < bytes; ++i) {
    hash ^= p[i];
    hash *= 1099511628211ULL;
  }
  return hash;
}

void writeReferenceData(FILE *out, const std::vector<cv::Point2f> &points,
                        const cv::Mat &descriptors) {
  const uint32_t numPoints = static_cast<uint32_t>(points.size());
  std::fwrite(&numPoints, sizeof(numPoints), 1, out);
  if (numPoints == 0) return;
  std::vector<float> xy(numPoints * 2);
  for (uint32_t i = 0; i < numPoints; ++i) {
    xy[i * 2] = points[i].x;
    xy[i * 2 + 1] = points[i].y;
  }
  std::fwrite(xy.data(), sizeof(float), xy.size(), out);
  std::fwrite(descriptors.data, 1, numPoints * kOrbDescriptorBytes, out);
}

}  // namespace

std::string artHash(const cv::Mat &card) { return perceptualHash(artRegion(card)); }

HashIndex::~HashIndex() {
  if (orbFile_) std::fclose(orbFile_);
}

bool HashIndex::load(const std::string &path) {
  entries_.clear();
  orbOffsets_.clear();
  referenceCache_.clear();
  if (orbFile_) {
    std::fclose(orbFile_);
    orbFile_ = nullptr;
  }
  std::ifstream in(path);
  if (!in) return false;
  std::string line;
  uint64_t tsvHash = kFnvOffsetBasis;
  while (std::getline(in, line)) {
    tsvHash = fnv1a64(tsvHash, line.c_str(), line.size());
    tsvHash = fnv1a64(tsvHash, "\n", 1);
    std::istringstream row(line);
    Entry entry;
    if (!std::getline(row, entry.setCode, '\t')) continue;
    if (!std::getline(row, entry.collector, '\t')) continue;
    if (!std::getline(row, entry.name, '\t')) continue;
    if (!std::getline(row, entry.fullHex, '\t')) continue;
    if (!std::getline(row, entry.artHex, '\t')) continue;
    if (!std::getline(row, entry.path, '\t')) entry.path.clear();
    if (entry.artHex.empty() || entry.fullHex.empty()) continue;
    entries_.push_back(std::move(entry));
  }
  // The sidecar with precomputed ORB references is optional: without it the reference jpg
  // cache is read on demand, with it the jpgs are not needed at run time at all.
  FILE *orb = std::fopen((path + ".orb").c_str(), "rb");
  if (!orb) return !entries_.empty();
  char magic[8];
  uint32_t count = 0;
  uint64_t storedHash = 0;
  if (std::fread(magic, 1, 8, orb) != 8 || std::memcmp(magic, kOrbMagic, 8) != 0 ||
      std::fread(&count, sizeof(count), 1, orb) != 1 || count != entries_.size() ||
      std::fread(&storedHash, sizeof(storedHash), 1, orb) != 1 || storedHash != tsvHash) {
    if (std::getenv("CARDSCAN_DEBUG_COUNT")) {
      std::fprintf(stderr, "index: unusable %s.orb (magic/count/checksum), falling back\n",
                   path.c_str());
    }
    std::fclose(orb);
    return !entries_.empty();
  }
  std::vector<uint64_t> offsets(count);
  if (std::fread(offsets.data(), sizeof(uint64_t), count, orb) != count) {
    std::fclose(orb);
    return !entries_.empty();
  }
  for (size_t i = 0; i < entries_.size(); ++i) orbOffsets_[entries_[i].path] = offsets[i];
  orbFile_ = orb;
  return !entries_.empty();
}

HashIndex::Reference HashIndex::computeReference(const cv::Mat &art) {
  Reference reference;
  if (art.empty()) return reference;
  auto orb = cv::ORB::create(kOrbKeypoints);
  std::vector<cv::KeyPoint> keys;
  orb->detectAndCompute(art, cv::noArray(), keys, reference.descriptors);
  reference.points.reserve(keys.size());
  for (const auto &key : keys) reference.points.push_back(key.pt);
  return reference;
}

bool HashIndex::readReference(uint64_t offset, Reference &out) const {
  if (std::fseek(orbFile_, static_cast<long>(offset), SEEK_SET) != 0) return false;
  uint32_t numPoints = 0;
  if (std::fread(&numPoints, sizeof(numPoints), 1, orbFile_) != 1) return false;
  if (numPoints == 0 || numPoints > 4096) return false;
  std::vector<float> xy(numPoints * 2);
  if (std::fread(xy.data(), sizeof(float), xy.size(), orbFile_) != xy.size()) return false;
  out.points.resize(numPoints);
  for (uint32_t i = 0; i < numPoints; ++i) {
    out.points[i] = cv::Point2f(xy[i * 2], xy[i * 2 + 1]);
  }
  out.descriptors.create(static_cast<int>(numPoints), static_cast<int>(kOrbDescriptorBytes),
                          CV_8U);
  if (std::fread(out.descriptors.data, 1, numPoints * kOrbDescriptorBytes, orbFile_) !=
      numPoints * kOrbDescriptorBytes) {
    out = Reference{};
    return false;
  }
  return true;
}

const HashIndex::Reference *HashIndex::referenceFor(const std::string &path) const {
  auto cached = referenceCache_.find(path);
  if (cached != referenceCache_.end()) {
    return cached->second.descriptors.empty() ? nullptr : &cached->second;
  }
  Reference reference;
  bool have = false;
  if (orbFile_) {
    auto slot = orbOffsets_.find(path);
    if (slot != orbOffsets_.end()) have = readReference(slot->second, reference);
  } else if (!path.empty()) {
    const cv::Mat image = cv::imread(path, cv::IMREAD_COLOR);
    if (!image.empty()) {
      reference = computeReference(artRegion(image));
      have = !reference.descriptors.empty();
    }
  }
  Reference &stored = referenceCache_[path];
  if (have) stored = std::move(reference);
  if (std::getenv("CARDSCAN_DEBUG_COUNT") && !have) {
    std::fprintf(stderr, "verify: cannot read reference %s\n", path.c_str());
  }
  return have ? &stored : nullptr;
}

ArtVerification HashIndex::verifyArt(const cv::Mat &card, const IndexMatch &match) const {
  ArtVerification result;
  if (card.empty() || match.path.empty()) return result;
  const Reference *reference = referenceFor(match.path);
  if (!reference || reference->descriptors.empty()) return result;
  const cv::Mat query = artRegion(card);
  if (query.empty()) return result;

  auto orb = cv::ORB::create(kOrbKeypoints);
  std::vector<cv::KeyPoint> queryKeys;
  cv::Mat queryDesc;
  orb->detectAndCompute(query, cv::noArray(), queryKeys, queryDesc);
  if (queryDesc.empty()) return result;

  cv::BFMatcher matcher(cv::NORM_HAMMING);
  std::vector<std::vector<cv::DMatch>> knn;
  matcher.knnMatch(queryDesc, reference->descriptors, knn, 2);
  std::vector<cv::Point2f> from;
  std::vector<cv::Point2f> to;
  for (const auto &pair : knn) {
    if (pair.size() < 2) continue;
    if (pair[0].distance >= 0.78f * pair[1].distance) continue;
    from.push_back(queryKeys[pair[0].queryIdx].pt);
    to.push_back(reference->points[pair[0].trainIdx]);
  }
  result.goodMatches = static_cast<int>(from.size());
  if (std::getenv("CARDSCAN_DEBUG_COUNT")) {
    std::fprintf(stderr, "verify %s:%s keys=%zu/%zu good=%d\n", match.setCode.c_str(),
                 match.collector.c_str(), queryKeys.size(), reference->points.size(),
                 result.goodMatches);
  }
  if (from.size() < 8) return result;
  cv::Mat inlierMask;
  cv::findHomography(from, to, cv::RANSAC, 4.0, inlierMask);
  if (!inlierMask.empty()) result.inliers = cv::countNonZero(inlierMask);
  return result;
}

std::vector<IndexMatch> HashIndex::lookup(const std::string &fullHex, const std::string &artHex,
                                          int topK) const {
  return lookup(fullHex, artHex, topK, {});
}

std::vector<IndexMatch> HashIndex::lookupInSet(const std::string &fullHex,
                                               const std::string &artHex,
                                               const std::string &setCode, int topK) const {
  // A reprint shares the artwork with the original print, so the global ranking can put a
  // different set first. Once the collector line names the set, the print is searched there.
  if (setCode.empty()) return {};
  return lookup(fullHex, artHex, topK, setCode);
}

std::vector<IndexMatch> HashIndex::lookup(const std::string &fullHex, const std::string &artHex,
                                          int topK, const std::string &onlySet) const {
  std::vector<IndexMatch> matches;
  if (fullHex.empty() || artHex.empty()) return matches;
  matches.reserve(entries_.size());
  for (const auto &entry : entries_) {
    if (!onlySet.empty() && entry.setCode != onlySet) continue;
    IndexMatch m;
    m.setCode = entry.setCode;
    m.collector = entry.collector;
    m.name = entry.name;
    m.path = entry.path;
    m.fullDistance = hashDistance(fullHex, entry.fullHex);
    m.artDistance = hashDistance(artHex, entry.artHex);
    matches.push_back(std::move(m));
  }
  std::sort(matches.begin(), matches.end(), [](const IndexMatch &a, const IndexMatch &b) {
    if (a.artDistance != b.artDistance) return a.artDistance < b.artDistance;
    return a.fullDistance < b.fullDistance;
  });
  if (topK > 0 && static_cast<int>(matches.size()) > topK) matches.resize(topK);
  return matches;
}

std::vector<IndexMatch> HashIndex::lookupCard(const cv::Mat &card, int topK) const {
  if (card.empty()) return {};
  return lookup(perceptualHash(card), artHash(card), topK);
}

namespace {

std::string normalizeNumber(const std::string &number) {
  size_t start = 0;
  while (start + 1 < number.size() && number[start] == '0') ++start;
  return number.substr(start);
}

std::string normalizeName(const std::string &name) {
  std::string out;
  for (unsigned char ch : name) {
    if (ch >= 0x80) continue;
    const char c = static_cast<char>(std::toupper(ch));
    if (std::isalnum(static_cast<unsigned char>(c))) {
      out.push_back(c);
    } else if (!out.empty() && out.back() != ' ') {
      out.push_back(' ');
    }
  }
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

}  // namespace

std::vector<IndexMatch> HashIndex::findByName(const std::string &name, int topK) const {
  std::vector<IndexMatch> matches;
  const std::string wanted = normalizeName(name);
  if (wanted.size() < 4) return matches;
  for (const auto &entry : entries_) {
    const std::string candidate = normalizeName(entry.name);
    if (candidate.empty()) continue;
    if (candidate != wanted && candidate.find(wanted) == std::string::npos &&
        wanted.find(candidate) == std::string::npos) {
      continue;
    }
    IndexMatch m;
    m.setCode = entry.setCode;
    m.collector = entry.collector;
    m.name = entry.name;
    m.path = entry.path;
    m.artDistance = candidate == wanted ? 0 : 1;
    m.fullDistance = 0;
    matches.push_back(std::move(m));
  }
  std::sort(matches.begin(), matches.end(), [](const IndexMatch &a, const IndexMatch &b) {
    if (a.artDistance != b.artDistance) return a.artDistance < b.artDistance;
    return a.collector < b.collector;
  });
  if (topK > 0 && static_cast<int>(matches.size()) > topK) matches.resize(topK);
  return matches;
}

std::string HashIndex::setWithNumber(const std::vector<std::string> &codes,
                                     const std::string &number, double maxDistance) const {
  if (number.empty() || codes.empty()) return {};
  const std::string wanted = normalizeNumber(number);
  std::string best;
  double bestDistance = maxDistance + 1e-9;
  for (const auto &entry : entries_) {
    if (normalizeNumber(entry.collector) != wanted) continue;
    for (const auto &code : codes) {
      if (code.empty()) continue;
      if (entry.setCode == code) return entry.setCode;
      const double distance = codeDistance(code, entry.setCode);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = entry.setCode;
      }
    }
  }
  return best;
}

std::string HashIndex::setWithNumber(const std::string &code, const std::string &number,
                                     double maxDistance) const {
  return setWithNumber(std::vector<std::string>{code}, number, maxDistance);
}

bool buildHashIndex(const std::string &metaPath, const std::string &outPath) {
  std::ifstream in(metaPath);
  if (!in) return false;
  std::ofstream out(outPath);
  if (!out) return false;
  // Reference data lands in a ".orb" sidecar next to the TSV: one record per TSV line, in
  // the same order, so run-time verification never needs the source jpgs again.
  const std::string orbPath = outPath + ".orb";
  const std::string partPath = orbPath + ".part";
  FILE *data = std::fopen(partPath.c_str(), "wb");
  std::vector<uint64_t> offsets;
  std::unordered_set<std::string> seenPaths;
  uint64_t tsvHash = kFnvOffsetBasis;
  std::string line;
  int written = 0;
  while (std::getline(in, line)) {
    const std::string path = jsonField(line, "path");
    if (path.empty()) continue;
    // Several Scryfall printings share one cached jpg; one TSV line per image keeps the
    // shortlist free of self-duplicates (the margin rule compares the top candidates).
    if (!seenPaths.insert(path).second) continue;
    const cv::Mat image = cv::imread(path, cv::IMREAD_COLOR);
    if (image.empty()) continue;
    const HashIndex::Reference reference = HashIndex::computeReference(artRegion(image));
    std::string row = jsonField(line, "set");
    row += '\t';
    row += jsonField(line, "collector");
    row += '\t';
    row += jsonField(line, "name");
    row += '\t';
    row += perceptualHash(image);
    row += '\t';
    row += artHash(image);
    row += '\t';
    row += path;
    row += '\n';
    out << row;
    tsvHash = fnv1a64(tsvHash, row.c_str(), row.size());
    if (data) {
      offsets.push_back(static_cast<uint64_t>(std::ftell(data)));
      writeReferenceData(data, reference.points, reference.descriptors);
    }
    ++written;
  }
  if (data) {
    std::fclose(data);
    FILE *orb = std::fopen(orbPath.c_str(), "wb");
    FILE *src = orb ? std::fopen(partPath.c_str(), "rb") : nullptr;
    bool sidecar = false;
    if (orb && src) {
      const uint32_t count = static_cast<uint32_t>(offsets.size());
      // Offsets were taken in the part file; in the final file the header precedes the
      // records, so each one shifts by the header size.
      const uint64_t headerBytes =
          8 + sizeof(count) + sizeof(tsvHash) + sizeof(uint64_t) * count;
      std::vector<uint64_t> absolute(offsets.size());
      for (size_t i = 0; i < offsets.size(); ++i) absolute[i] = headerBytes + offsets[i];
      sidecar = std::fwrite(kOrbMagic, 1, 8, orb) == 8 &&
                std::fwrite(&count, sizeof(count), 1, orb) == 1 &&
                std::fwrite(&tsvHash, sizeof(tsvHash), 1, orb) == 1 &&
                std::fwrite(absolute.data(), sizeof(uint64_t), absolute.size(), orb) ==
                    absolute.size();
      char buffer[65536];
      size_t chunk;
      while (sidecar && (chunk = std::fread(buffer, 1, sizeof(buffer), src)) > 0) {
        sidecar = std::fwrite(buffer, 1, chunk, orb) == chunk;
      }
    }
    if (orb) std::fclose(orb);
    if (src) std::fclose(src);
    std::remove(partPath.c_str());
    if (written > 0) {
      if (sidecar) {
        std::fprintf(stderr, "index: %d cards, %s.orb written\n", written, outPath.c_str());
      } else {
        std::fprintf(stderr, "index: %d cards, %s.orb FAILED\n", written, outPath.c_str());
      }
      return true;
    }
  }
  std::fprintf(stderr, "index: %d cards\n", written);
  return written > 0;
}

}  // namespace cardscan
