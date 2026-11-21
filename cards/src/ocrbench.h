#pragma once

#include <string>

namespace cardscan {

// Ranks recognition variants on synthetic collector lines cut from the Scryfall cache:
// each card image is cropped to its collector line, degraded to the resolution the detector
// sees on a 720p video (10-16 px line height + blur + noise) and read by every variant.
int runOcrBenchmark(const std::string &metaPath, int samples);

}  // namespace cardscan
