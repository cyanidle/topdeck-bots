#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "detector.h"
#include "hashindex.h"
#include "identity.h"
#include "ocr.h"
#include "phash.h"
#include "tracker.h"

namespace cardscan {

struct AnalyzerOptions {
  DetectorParams detector;
  OcrParams ocr;
  bool enableOcr = true;
  bool enableTitleFallback = true;
  int minTrackHits = 4;
  int minTextRuns = 2;
  int strongTextRuns = 5;
  double minStructuralContrast = 0.06;
  double cropBottomExpansion = 0.09;
  double cropBottomExpansionMax = 0.30;
  double cropBottomExpansionStep = 0.06;
  int serialGrowSteps = 4;
  int stride = 1;
  int maxFrames = 0;
  int ocrMaxAttemptsPerTrack = 8;
  int ocrMinFrameGap = 4;
  int maxOcrScreensPerFrame = 3;
  int ocrStride = 2;
  int minSegmentReads = 5;
  double segmentGapSeconds = 0.6;
  double segmentCodeDistance = 2.5;
  double segmentNumberCodeTolerance = 1.1;
  int segmentIncompatibleRun = 3;
  int minNumberSupport = 2;
  double minNumberAgreement = 0.25;
  double maxSegmentSeconds = 2.5;
  double ocrMinQuality = 0.25;
  double nestedIou = 0.60;
  double nestedContainment = 0.85;
  int hashMergeDistance = 16;
  double hashMergeGapSeconds = 0.5;
  std::string phashIndexPath;
  int indexMaxDistance = 26;
  int indexMaxFullDistance = 28;
  int indexMinMargin = 2;
  int indexMinSupport = 2;
  std::string debugDir;
  std::string overlayPath;
  int debugFrameEvery = 30;
  bool saveTrackCrops = true;
  bool verbose = false;
};

struct CardResult {
  int trackId = -1;
  std::string setCode;
  std::string number;
  std::string total;
  std::string language;
  std::string rarity;
  std::string name;
  std::string identity;  // "DDS:051" (codes) or "N:NAME" (title fallback), empty if unknown
  std::string identifiedBy;  // "codes", "index", "title"
  double confidence = 0.0;
  double quality = 0.0;
  int hits = 0;
  int firstFrame = 0;
  int bestFrame = 0;
  int lastFrame = 0;
  double firstTs = 0.0;
  double bestTs = 0.0;
  double lastTs = 0.0;
  bool identified = false;
  bool identifiedByCodes = false;
  std::vector<cv::Point2f> quad;
  std::string hash;
  std::vector<int> mergedTrackIds;
};

struct ImageResult {
  std::string input;
  int width = 0;
  int height = 0;
  std::vector<CardResult> cards;
};

struct VideoResult {
  std::string input;
  int width = 0;
  int height = 0;
  double fps = 0.0;
  double duration = 0.0;
  int frames = 0;
  int processedFrames = 0;
  std::vector<CardResult> cards;
  double elapsedSeconds = 0.0;
  int screenedTracks = 0;
  int readsTotal = 0;
  int readsWithIdentity = 0;
  std::vector<ReadSample> reads;
};

class Analyzer {
 public:
  explicit Analyzer(const AnalyzerOptions &options);

  ImageResult analyzeImage(const std::string &path);
  VideoResult analyzeVideo(const std::string &path);

  const AnalyzerOptions &options() const { return opt_; }

 private:
  AnalyzerOptions opt_;
  HashIndex index_;
};

bool hasImageExtension(const std::string &path);
std::string voteDigits(const std::vector<std::string> &values);
std::string normalizeCollector(const std::string &collector);
std::vector<CardResult> buildCardsFromReads(const std::vector<ReadSample> &reads,
                                            const AnalyzerOptions &opt,
                                            const HashIndex *index = nullptr);
void consolidateCards(std::vector<CardResult> &cards);
std::vector<ReadSample> loadReadTimeline(const std::string &path);

}
