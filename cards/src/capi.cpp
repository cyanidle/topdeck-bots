#include "capi.h"

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "analyzer.h"
#include "report.h"

struct CardscanHandle {
  cardscan::AnalyzerOptions options;
  std::string lastError;
};

// Some distributions ship libspatialite (pulled in by libgdal -> libopencv_imgcodecs)
// with a reference to this libxml2 symbol, which libxml2 >= 2.13 no longer exports.
// Executables tolerate the missing symbol, but dlopen() of a shared library does not,
// so the ctypes binding could not load us. We never touch spatialite; the stub only
// satisfies the loader.
extern "C" __attribute__((weak)) void xmlNanoHTTPCleanup(void) {}

namespace {

char *copyString(const std::string &text) {
  char *out = static_cast<char *>(std::malloc(text.size() + 1));
  if (!out) return nullptr;
  std::memcpy(out, text.c_str(), text.size() + 1);
  return out;
}

}  // namespace

CardscanHandle *cardscan_create(void) {
  auto *handle = new CardscanHandle();
  return handle;
}

void cardscan_destroy(CardscanHandle *handle) { delete handle; }

const char *cardscan_version(void) { return "0.1.0"; }

int cardscan_set_option(CardscanHandle *handle, const char *key, const char *value) {
  if (!handle || !key || !value) return -1;
  const std::string k(key);
  const std::string v(value);
  try {
    if (k == "ocr") {
      handle->options.enableOcr = v != "0";
    } else if (k == "title_fallback") {
      handle->options.enableTitleFallback = v != "0";
    } else if (k == "min_track_hits") {
      handle->options.minTrackHits = std::stoi(v);
    } else if (k == "ocr_stride") {
      handle->options.ocrStride = std::stoi(v);
    } else if (k == "max_frames") {
      handle->options.maxFrames = std::stoi(v);
    } else if (k == "stride") {
      handle->options.stride = std::stoi(v);
    } else if (k == "phash_index") {
      handle->options.phashIndexPath = v;
    } else if (k == "debug_dir") {
      handle->options.debugDir = v;
    } else if (k == "overlay") {
      handle->options.overlayPath = v;
    } else {
      return -1;
    }
  } catch (...) {
    return -1;
  }
  return 0;
}

int cardscan_set_set_codes(CardscanHandle *handle, const char *const *codes, int count) {
  if (!handle || count < 0) return -1;
  handle->options.ocr.knownSetCodes.clear();
  for (int i = 0; i < count; ++i) {
    if (codes[i] && *codes[i]) handle->options.ocr.knownSetCodes.emplace_back(codes[i]);
  }
  return 0;
}

char *cardscan_analyze(CardscanHandle *handle, const char *path) {
  if (!handle || !path) return nullptr;
  handle->lastError.clear();
  try {
    cardscan::Analyzer analyzer(handle->options);
    const std::string input(path);
    std::string json;
    if (cardscan::hasImageExtension(input)) {
      json = cardscan::imageResultJson(analyzer.analyzeImage(input));
    } else {
      json = cardscan::videoResultJson(analyzer.analyzeVideo(input));
    }
    return copyString(json);
  } catch (const std::exception &e) {
    handle->lastError = e.what();
    return nullptr;
  } catch (...) {
    handle->lastError = "unknown error";
    return nullptr;
  }
}

const char *cardscan_last_error(CardscanHandle *handle) {
  return handle ? handle->lastError.c_str() : "";
}

void cardscan_free_string(char *text) { std::free(text); }
