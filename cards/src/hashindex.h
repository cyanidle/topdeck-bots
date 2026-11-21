#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

namespace cardscan {

struct IndexMatch {
  std::string setCode;
  std::string collector;
  std::string name;
  int artDistance = 0;
  int fullDistance = 0;
};

std::string artHash(const cv::Mat &card);

class HashIndex {
 public:
  bool load(const std::string &path);
  bool empty() const { return entries_.empty(); }
  size_t size() const { return entries_.size(); }

  std::vector<IndexMatch> lookup(const std::string &fullHex, const std::string &artHex,
                                 int topK) const;
  std::vector<IndexMatch> lookupCard(const cv::Mat &card, int topK) const;

 private:
  struct Entry {
    std::string setCode;
    std::string collector;
    std::string name;
    std::string fullHex;
    std::string artHex;
  };
  std::vector<Entry> entries_;
};

bool buildHashIndex(const std::string &metaPath, const std::string &outPath);

}  // namespace cardscan
