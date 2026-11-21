#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "detector.h"
#include "identity.h"
namespace cardscan {

struct TrackRead {
  int frame = 0;
  double ts = 0.0;
  double quality = 0.0;
  OcrRead read;
};

struct ReadSample {
  int frame = 0;
  double ts = 0.0;
  double quality = 1.0;
  std::string identity;
  std::string code;
  std::string number;
  std::string total;
  std::string language;
  std::string rarity;
  std::string name;
  std::string hash;
  std::vector<cv::Point2f> quad;
};

struct Track {
  int id = 0;
  int firstFrame = 0;
  int lastFrame = 0;
  int hits = 0;
  int misses = 0;
  double firstTs = 0.0;
  double lastTs = 0.0;

  Quad lastQuad{};
  Quad bestQuad{};
  int bestFrame = 0;
  double bestTs = 0.0;
  double bestQuality = -1.0;
  int bestTextRuns = 0;
  double bestStructural = 0.0;
  cv::Mat bestCrop;

  std::string lastHash;
  std::vector<TrackRead> reads;
  std::string title;
  int titleAttempts = 0;
  int ocrAttempts = 0;
  int lastOcrFrame = -100000;

  bool confirmed(int minHits) const { return hits >= minHits; }
};

class Tracker {
 public:
  struct Params {
    int maxGapFrames = 25;
    double minIou = 0.25;
    double maxCenterShift = 0.35;
    double minAreaRatio = 0.45;
    int maxHashDistance = 18;
  };

  Tracker() = default;
  explicit Tracker(Params p) : p_(p) {}

  int match(int frame, const Detection &d, const std::string &hash) const;
  int update(int frame, double ts, const Detection &d, const std::string &hash);
  void endFrame(int frame);

  Track *get(int id);
  std::vector<Track> &tracks() { return tracks_; }
  const std::vector<Track> &tracks() const { return tracks_; }

 private:
  Params p_;
  int nextId_ = 1;
  std::vector<Track> tracks_;
};

}
