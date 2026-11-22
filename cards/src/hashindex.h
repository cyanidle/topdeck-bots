#pragma once

#include <map>
#include <string>
#include <vector>

#include <opencv2/core.hpp>

namespace cardscan {

struct IndexMatch {
  std::string setCode;
  std::string collector;
  std::string name;
  std::string path;
  int artDistance = 0;
  int fullDistance = 0;
};

std::string artHash(const cv::Mat &card);

// Keypoint agreement between a camera crop and the indexed reference image. The artwork
// hash narrows the search down to a couple of candidates, but it is not precise enough to
// decide on its own: only an actual feature match proves that two pictures show one card.
struct ArtVerification {
  int goodMatches = 0;
  int inliers = 0;
};

class HashIndex {
 public:
  bool load(const std::string &path);
  bool empty() const { return entries_.empty(); }
  size_t size() const { return entries_.size(); }

  std::vector<IndexMatch> lookup(const std::string &fullHex, const std::string &artHex,
                                 int topK) const;
  std::vector<IndexMatch> lookupInSet(const std::string &fullHex, const std::string &artHex,
                                      const std::string &setCode, int topK) const;
  std::vector<IndexMatch> lookupCard(const cv::Mat &card, int topK) const;
  std::vector<IndexMatch> findByName(const std::string &name, int topK) const;
  ArtVerification verifyArt(const cv::Mat &card, const IndexMatch &match) const;
  // The index knows which (set, number) pairs exist, so a misread set code ("TRTT" for
  // "TMT") can be resolved by asking which nearby set actually has that collector number.
  std::string setWithNumber(const std::vector<std::string> &codes, const std::string &number,
                            double maxDistance) const;
  std::string setWithNumber(const std::string &code, const std::string &number,
                            double maxDistance) const;

 private:
  std::vector<IndexMatch> lookup(const std::string &fullHex, const std::string &artHex,
                                 int topK, const std::string &onlySet) const;

  struct Entry {
    std::string setCode;
    std::string collector;
    std::string name;
    std::string fullHex;
    std::string artHex;
    std::string path;
  };
  std::vector<Entry> entries_;
  mutable std::map<std::string, cv::Mat> referenceCache_;
};

bool buildHashIndex(const std::string &metaPath, const std::string &outPath);

}  // namespace cardscan
