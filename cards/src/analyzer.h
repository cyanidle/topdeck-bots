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
  // Brightness rise under the card's black border, as a share of the way back to the card body:
  // the content of the card ends there, which is what keeps the collector line of a card lying
  // under this one out of the OCR search (see contentBottomFromBorder).
  double contentRiseRatio = 0.5;
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
  // A cleanly read known set code this far from the segment's majority starts a new show
  // when the majority has been silent for a full segmentGapSeconds (unreadable frames
  // between two presentations instead of an incompatible-code run).
  double segmentShowDistance = 1.5;
  double segmentNumberCodeTolerance = 1.1;
  int segmentIncompatibleRun = 3;
  int minNumberSupport = 2;
  int minNumberSupportLoose = 3;
  double minNumberAgreement = 0.25;
  double setSnapDistance = 2.0;
  double duplicateMergeGapSeconds = 0.30;
  double maxSegmentSeconds = 2.5;
  double ocrMinQuality = 0.25;
  double nestedIou = 0.60;
  double nestedContainment = 0.85;
  int hashMergeDistance = 16;
  double hashMergeGapSeconds = 0.5;
  std::string phashIndexPath;
  // The artwork hash only picks a shortlist: the same card photographed at an angle sits
  // about as far from its reference image as an unrelated card does, so the identity is
  // decided by a keypoint match (indexMinInliers) against the reference artwork.
  int indexShortlistDistance = 22;
  int indexVerifyCandidates = 3;
  int indexMinInliers = 12;
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

// One held-up card as it was seen best in the feed: the detected quad plus the strip below it
// that holds the collector line, warped to the card aspect ratio. Rectangles of one
// presentation collapse into the sharpest one, so the list is "one rectangle per presentation".
struct CardCrop {
  int id = 0;
  int firstFrame = 0;
  int bestFrame = 0;
  int lastFrame = 0;
  double firstTs = 0.0;
  double bestTs = 0.0;
  double lastTs = 0.0;
  int hits = 0;
  double quality = 0.0;
  double contentBottom = 1.0;
  std::string hash;
  std::string artHash;
  std::vector<cv::Point2f> quad;
  cv::Mat image;
};

struct CropExtraction {
  std::string input;
  int width = 0;
  int height = 0;
  double fps = 0.0;
  int frames = 0;
  int processedFrames = 0;
  std::vector<CardCrop> crops;
};

struct CropRecognition {
  std::string file;
  int id = 0;
  int firstFrame = 0;
  int lastFrame = 0;
  std::string setCode;
  std::string number;
  std::string identity;
  std::string sources;
  std::string indexIdentity;
  std::string name;
  std::string raw;
  double confidence = 0.0;
  int score = 0;
};

// The detector is the expensive half, so it runs once and writes the normalized card
// rectangles; recognition and the checks then work on those few images instead of the video.
CropExtraction extractCardCrops(const std::string &videoPath, const std::string &outDir,
                                const AnalyzerOptions &opt, const std::vector<int> &frames = {},
                                int frameSpan = 0);

class Analyzer {
 public:
  explicit Analyzer(const AnalyzerOptions &options);

  ImageResult analyzeImage(const std::string &path);
  VideoResult analyzeVideo(const std::string &path);
  std::vector<CropRecognition> recognizeCards(const std::string &dir);

  const AnalyzerOptions &options() const { return opt_; }

 private:
  AnalyzerOptions opt_;
  HashIndex index_;
};

bool hasImageExtension(const std::string &path);
std::string voteDigits(const std::vector<std::string> &values);
std::vector<CardResult> buildCardsFromReads(const std::vector<ReadSample> &reads,
                                            const AnalyzerOptions &opt,
                                            const HashIndex *index = nullptr);
void consolidateCards(std::vector<CardResult> &cards, const AnalyzerOptions &opt);
std::vector<ReadSample> loadReadTimeline(const std::string &path);

}
