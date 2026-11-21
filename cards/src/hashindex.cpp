#include "hashindex.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <sstream>

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

}  // namespace

std::string artHash(const cv::Mat &card) { return perceptualHash(artRegion(card)); }

bool HashIndex::load(const std::string &path) {
  entries_.clear();
  std::ifstream in(path);
  if (!in) return false;
  std::string line;
  while (std::getline(in, line)) {
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
  return !entries_.empty();
}

ArtVerification HashIndex::verifyArt(const cv::Mat &card, const IndexMatch &match) const {
  ArtVerification result;
  if (card.empty() || match.path.empty()) return result;
  cv::Mat &reference = referenceCache_[match.path];
  if (reference.empty()) {
    reference = cv::imread(match.path, cv::IMREAD_COLOR);
    if (reference.empty()) {
      if (std::getenv("CARDSCAN_DEBUG_COUNT")) {
        std::fprintf(stderr, "verify: cannot read reference %s\n", match.path.c_str());
      }
      return result;
    }
    reference = artRegion(reference);
  }
  const cv::Mat query = artRegion(card);
  if (query.empty() || reference.empty()) return result;

  auto orb = cv::ORB::create(700);
  std::vector<cv::KeyPoint> queryKeys;
  std::vector<cv::KeyPoint> referenceKeys;
  cv::Mat queryDesc;
  cv::Mat referenceDesc;
  orb->detectAndCompute(query, cv::noArray(), queryKeys, queryDesc);
  orb->detectAndCompute(reference, cv::noArray(), referenceKeys, referenceDesc);
  if (queryDesc.empty() || referenceDesc.empty()) return result;

  cv::BFMatcher matcher(cv::NORM_HAMMING);
  std::vector<std::vector<cv::DMatch>> knn;
  matcher.knnMatch(queryDesc, referenceDesc, knn, 2);  std::vector<cv::Point2f> from;
  std::vector<cv::Point2f> to;
  for (const auto &pair : knn) {
    if (pair.size() < 2) continue;
    if (pair[0].distance >= 0.78f * pair[1].distance) continue;
    from.push_back(queryKeys[pair[0].queryIdx].pt);
    to.push_back(referenceKeys[pair[0].trainIdx].pt);
  }
  result.goodMatches = static_cast<int>(from.size());
  if (std::getenv("CARDSCAN_DEBUG_COUNT")) {
    std::fprintf(stderr, "verify %s:%s keys=%zu/%zu good=%d\n", match.setCode.c_str(),
                 match.collector.c_str(), queryKeys.size(), referenceKeys.size(),
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
  std::vector<IndexMatch> matches;
  if (fullHex.empty() || artHex.empty()) return matches;
  matches.reserve(entries_.size());
  for (const auto &entry : entries_) {
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

std::string HashIndex::setWithNumber(const std::string &code, const std::string &number,
                                     double maxDistance) const {
  if (number.empty()) return {};
  const std::string wanted = normalizeNumber(number);
  std::string best;
  double bestDistance = maxDistance + 1e-9;
  for (const auto &entry : entries_) {
    if (normalizeNumber(entry.collector) != wanted) continue;
    if (entry.setCode == code) return entry.setCode;
    const double distance = codeDistance(code, entry.setCode);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = entry.setCode;
    }
  }
  return best;
}

bool buildHashIndex(const std::string &metaPath, const std::string &outPath) {
  std::ifstream in(metaPath);
  if (!in) return false;
  std::ofstream out(outPath);
  if (!out) return false;
  std::string line;
  int written = 0;
  while (std::getline(in, line)) {
    const std::string path = jsonField(line, "path");
    if (path.empty()) continue;
    const cv::Mat image = cv::imread(path, cv::IMREAD_COLOR);
    if (image.empty()) continue;
    out << jsonField(line, "set") << '\t' << jsonField(line, "collector") << '\t'
        << jsonField(line, "name") << '\t' << perceptualHash(image) << '\t' << artHash(image)
        << '\t' << path << '\n';
    ++written;
  }
  std::fprintf(stderr, "index: %d cards\n", written);
  return written > 0;
}

}  // namespace cardscan
