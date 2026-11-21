#pragma once

#include <string>

#include "analyzer.h"

namespace cardscan {

std::string imageResultJson(const ImageResult &result);
std::string videoResultJson(const VideoResult &result);

std::string formatImageSummary(const ImageResult &result);
std::string formatVideoSummary(const VideoResult &result);

}
