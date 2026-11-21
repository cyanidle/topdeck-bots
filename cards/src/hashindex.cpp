#include "hashindex.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <sstream>

#include <opencv2/imgcodecs.hpp>

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
    if (entry.artHex.empty() || entry.fullHex.empty()) continue;
    entries_.push_back(std::move(entry));
  }
  return !entries_.empty();
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
