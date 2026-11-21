#include "ocr.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include <sys/stat.h>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include <tesseract/capi.h>

namespace cardscan {
namespace {

bool fileExists(const std::string &path) {
  struct stat st;
  return ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

std::string findTessdata() {
  const char *env = std::getenv("TESSDATA_PREFIX");
  std::vector<std::string> candidates;
  if (env && *env) candidates.push_back(env);
  candidates.push_back("/usr/share/tesseract-ocr/5/tessdata");
  candidates.push_back("/usr/share/tesseract-ocr/4.00/tessdata");
  candidates.push_back("/usr/share/tessdata");
  candidates.push_back("/usr/local/share/tessdata");
  for (const auto &c : candidates) {
    if (fileExists(c + "/eng.traineddata")) return c;
  }
  return {};
}

inline TessBaseAPI *asApi(void *p) { return static_cast<TessBaseAPI *>(p); }

}  // namespace

CardRegions defaultRegions(const OcrParams &p) {
  const double w = p.cardWidth, h = p.cardHeight;
  CardRegions r;
  r.collectorLine = cv::Rect(static_cast<int>(0.025 * w), static_cast<int>(0.880 * h),
                             static_cast<int>(0.40 * w), static_cast<int>(0.050 * h));
  r.setLine = cv::Rect(static_cast<int>(0.025 * w), static_cast<int>(0.918 * h),
                       static_cast<int>(0.56 * w), static_cast<int>(0.048 * h));
  r.collectorBlock = cv::Rect(static_cast<int>(0.020 * w), static_cast<int>(0.872 * h),
                              static_cast<int>(0.58 * w), static_cast<int>(0.098 * h));
  r.title = cv::Rect(static_cast<int>(0.055 * w), static_cast<int>(0.042 * h),
                     static_cast<int>(0.74 * w), static_cast<int>(0.062 * h));
  return r;
}

CardOcr::CardOcr(const OcrParams &p) : p_(p), regions_(defaultRegions(p)) {
  const std::string dataPath = p_.dataPath.empty() ? findTessdata() : p_.dataPath;
  api_ = TessBaseAPICreate();
  if (!api_) {
    error_ = "TessBaseAPICreate failed";
    return;
  }
  const int rc = TessBaseAPIInit3(asApi(api_), dataPath.empty() ? nullptr : dataPath.c_str(),
                                  p_.language.c_str());
  if (rc != 0) {
    error_ = "TessBaseAPIInit3 failed for language '" + p_.language + "'";
    TessBaseAPIDelete(asApi(api_));
    api_ = nullptr;
    return;
  }
  TessBaseAPISetVariable(asApi(api_), "debug_file", "/dev/null");
  TessBaseAPISetVariable(asApi(api_), "user_defined_dpi", "300");
}

CardOcr::~CardOcr() {
  if (api_) {
    TessBaseAPIEnd(asApi(api_));
    TessBaseAPIDelete(asApi(api_));
  }
}

CollectorLayout locateCollector(const cv::Mat &cardWarped, const OcrParams &p) {
  CollectorLayout layout;
  if (cardWarped.empty()) return layout;
  cv::Mat gray;
  if (cardWarped.channels() == 3) {
    cv::cvtColor(cardWarped, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = cardWarped;
  }

  const cv::Rect search(static_cast<int>(gray.cols * 0.005),
                        static_cast<int>(gray.rows * p.collectorRoiTop),
                        static_cast<int>(gray.cols * p.collectorRoiWidth),
                        static_cast<int>(gray.rows * (1.0 - p.collectorRoiTop)));
  const cv::Rect roi = search & cv::Rect(0, 0, gray.cols, gray.rows);
  if (roi.width < 40 || roi.height < 20) return layout;

  const double scale = 3.0;
  cv::Mat up;
  cv::resize(gray(roi), up, cv::Size(), scale, scale, cv::INTER_CUBIC);

  cv::Mat enhanced;
  cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(3.0, cv::Size(8, 8));
  clahe->apply(up, enhanced);

  cv::Mat tophat;
  cv::morphologyEx(enhanced, tophat, cv::MORPH_TOPHAT,
                   cv::getStructuringElement(cv::MORPH_RECT, cv::Size(11, 11)));
  cv::normalize(tophat, tophat, 0, 255, cv::NORM_MINMAX);
  cv::Mat binary;
  cv::threshold(tophat, binary, 0, 255, cv::THRESH_BINARY | cv::THRESH_OTSU);
  cv::morphologyEx(binary, binary, cv::MORPH_CLOSE,
                   cv::getStructuringElement(cv::MORPH_RECT, cv::Size(2, 2)));

  std::vector<std::vector<cv::Point>> contours;
  cv::findContours(binary, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);

  std::vector<cv::Rect> glyphs;
  for (const auto &c : contours) {
    const cv::Rect b = cv::boundingRect(c);
    if (b.x <= 1 || b.y <= 1 || b.x + b.width >= binary.cols - 1) continue;
    const double hr = static_cast<double>(b.height) / binary.rows;
    const double wr = static_cast<double>(b.width) / binary.cols;
    if (hr < 0.020 || hr > 0.10) continue;
    if (wr < 0.004 || wr > 0.10) continue;
    if (cv::contourArea(c) < 6.0) continue;
    glyphs.push_back(b);
  }
  if (glyphs.size() < 3) return layout;

  std::sort(glyphs.begin(), glyphs.end(),
            [](const cv::Rect &a, const cv::Rect &b) { return a.y < b.y; });

  std::vector<std::vector<cv::Rect>> groups;
  for (const auto &g : glyphs) {
    bool placed = false;
    const int center = g.y + g.height / 2;
    for (auto &group : groups) {
      const cv::Rect &anchor = group.front();
      const int anchorCenter = anchor.y + anchor.height / 2;
      const int tolerance = std::max(4, anchor.height / 2);
      if (std::abs(center - anchorCenter) <= tolerance) {
        group.push_back(g);
        placed = true;
        break;
      }
    }
    if (!placed) groups.emplace_back(1, g);
  }

  std::vector<cv::Rect> lineBoxes;
  for (const auto &group : groups) {
    if (group.size() < 3) continue;
    cv::Rect box = group.front();
    for (const auto &g : group) box |= g;
    if (box.x > binary.cols * p.collectorMaxStartX) continue;
    lineBoxes.push_back(box);
  }
  if (std::getenv("CARDSCAN_DEBUG_OCR")) {
    cv::Mat visual;
    cv::cvtColor(up, visual, cv::COLOR_GRAY2BGR);
    for (const auto &g : glyphs) cv::rectangle(visual, g, cv::Scalar(0, 255, 0), 1);
    for (const auto &b : lineBoxes) cv::rectangle(visual, b, cv::Scalar(0, 0, 255), 2);
    cv::imwrite("/tmp/cardscan_layout.png", visual);
    cv::imwrite("/tmp/cardscan_layout_bin.png", binary);
  }
  if (lineBoxes.empty()) return layout;

  std::sort(lineBoxes.begin(), lineBoxes.end(),
            [](const cv::Rect &a, const cv::Rect &b) { return a.y > b.y; });

  // One printed line often yields several glyph groups (a set symbol or a wide gap splits
  // them), so glue groups that share a vertical band before picking the line to read.
  std::vector<cv::Rect> mergedLines;
  for (const cv::Rect &box : lineBoxes) {
    bool glued = false;
    for (cv::Rect &line : mergedLines) {
      const int overlap =
          std::min(line.y + line.height, box.y + box.height) - std::max(line.y, box.y);
      if (overlap > 0.5 * std::min(line.height, box.height)) {
        line |= box;
        glued = true;
        break;
      }
    }
    if (!glued) mergedLines.push_back(box);
  }
  std::sort(mergedLines.begin(), mergedLines.end(),
            [](const cv::Rect &a, const cv::Rect &b) { return a.y > b.y; });

  std::vector<cv::Rect> chosen;
  chosen.push_back(mergedLines.front());
  for (size_t i = 1; i < mergedLines.size() && chosen.size() < 2; ++i) {
    const cv::Rect &first = chosen.front();
    const cv::Rect &cand = mergedLines[i];
    if (cand.y + cand.height / 2 >= first.y + first.height / 2) continue;
    if (cand.y < first.y - static_cast<int>(first.height * 3.5)) continue;
    chosen.push_back(cand);
  }

  const cv::Rect &anchorLine = chosen.front();
  const int lineHeight = std::max(8, anchorLine.height);
  cv::Rect textUnion = chosen.front();
  for (const auto &line : chosen) textUnion |= line;
  // The warp is built from the detected quad, which can be shifted inside the card, so the
  // block follows the located text instead of a fixed left-edge fraction.
  const int padX = 3 * lineHeight;
  cv::Rect block(textUnion.x - padX, anchorLine.y - 2 * lineHeight,
                 textUnion.width + 2 * padX, static_cast<int>(3.8 * lineHeight));
  block &= cv::Rect(0, 0, binary.cols, binary.rows);

  layout.block = cv::Rect(
      roi.x + static_cast<int>(std::lround(block.x / scale)),
      roi.y + static_cast<int>(std::lround(block.y / scale)),
      static_cast<int>(std::lround(block.width / scale)),
      static_cast<int>(std::lround(block.height / scale)));
  layout.block &= cv::Rect(0, 0, gray.cols, gray.rows);
  for (const auto &line : chosen) {
    TextLineBox box;
    box.box = cv::Rect(roi.x + static_cast<int>(std::lround(line.x / scale)),
                       roi.y + static_cast<int>(std::lround(line.y / scale)),
                       static_cast<int>(std::lround(line.width / scale)),
                       static_cast<int>(std::lround(line.height / scale)));
    box.box &= cv::Rect(0, 0, gray.cols, gray.rows);
    layout.lines.push_back(box);
  }
  layout.found = layout.block.width > 20 && layout.block.height > 12;
  if (std::getenv("CARDSCAN_DEBUG_OCR")) {
    std::fprintf(stderr, "[layout] found=%d block=%d,%d %dx%d lines=%zu\n",
                 static_cast<int>(layout.found), layout.block.x, layout.block.y,
                 layout.block.width, layout.block.height, layout.lines.size());
    for (const auto &line : layout.lines) {
      std::fprintf(stderr, "  line: %d,%d %dx%d\n", line.box.x, line.box.y, line.box.width,
                   line.box.height);
    }
  }
  return layout;
}

cv::Mat CardOcr::prepare(const cv::Mat &gray, int mode, int upscale) {
  cv::Mat up;
  const cv::Size target(gray.cols * upscale, gray.rows * upscale);
  cv::resize(gray, up, target, 0, 0, cv::INTER_LANCZOS4);
  cv::Mat blurred;
  cv::GaussianBlur(up, blurred, cv::Size(0, 0), 1.1);
  cv::addWeighted(up, 1.6, blurred, -0.6, 0, up);
  cv::Mat out;
  if (mode == 0) {
    out = up;
  } else {
    cv::Mat work = up;
    if (mode == 2) {
      cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.5, cv::Size(8, 8));
      clahe->apply(work, work);
    }
    cv::Mat thr;
    if (mode == 1) {
      cv::adaptiveThreshold(work, thr, 255, cv::ADAPTIVE_THRESH_MEAN_C, cv::THRESH_BINARY_INV, 31,
                            12);
    } else {
      cv::threshold(work, thr, 0, 255, cv::THRESH_BINARY_INV | cv::THRESH_OTSU);
    }
    out = thr;
  }
  return out;
}

std::string CardOcr::run(const cv::Mat &gray, int psm) {
  if (!api_) return {};
  cv::Mat input = gray;
  if (!input.isContinuous()) input = input.clone();
  if (input.type() != CV_8UC1) cv::cvtColor(input, input, cv::COLOR_BGR2GRAY);
  TessBaseAPISetImage(asApi(api_), input.data, input.cols, input.rows, 1,
                      static_cast<int>(input.step));
  TessBaseAPISetPageSegMode(asApi(api_), static_cast<TessPageSegMode>(psm));
  char *text = TessBaseAPIGetUTF8Text(asApi(api_));
  std::string out;
  if (text) {
    out = text;
    TessDeleteText(text);
  }
  return out;
}

OcrRead CardOcr::readCard(const cv::Mat &cardWarped) {
  lastRawLog_.clear();
  if (!api_ || cardWarped.empty()) return {};

  cv::Mat gray;
  if (cardWarped.channels() == 3) {
    cv::cvtColor(cardWarped, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = cardWarped;
  }

  struct Attempt {
    cv::Rect region;
    int mode;
    int psm;
    int regionId;
  };
  std::vector<Attempt> attempts;
  const CollectorLayout layout = locateCollector(cardWarped, p_);
  if (layout.found) {
    const cv::Rect *setLine = layout.lines.empty() ? nullptr : &layout.lines[0].box;
    const cv::Rect *numberLine = layout.lines.size() > 1 ? &layout.lines[1].box : nullptr;
    auto tightBox = [](const cv::Rect &line, double widthFactor) {
      const int width = std::max(24, static_cast<int>(line.height * widthFactor));
      return cv::Rect(line.x - line.height, line.y - line.height / 2, width, line.height * 2);
    };

    attempts.push_back({layout.block, 0, 6, 0});
    if (numberLine) {
      attempts.push_back({*numberLine, 0, 7, 1});
      attempts.push_back({tightBox(*numberLine, 7.5), 0, 7, 1});
      attempts.push_back({tightBox(*numberLine, 7.5), 0, 8, 1});
    }
    if (setLine) attempts.push_back({*setLine, 0, 7, 2});
    if (numberLine) attempts.push_back({*numberLine, 2, 7, 1});
    if (setLine) {
      attempts.push_back({*setLine, 2, 7, 2});
      attempts.push_back({*setLine, 1, 7, 2});
    }
    if (numberLine) attempts.push_back({*numberLine, 1, 7, 1});
    if (setLine) attempts.push_back({tightBox(*setLine, 6.0), 0, 7, 2});
    attempts.push_back({layout.block, 1, 6, 0});
  } else {
    attempts = {
        {regions_.collectorBlock, 0, 6, 0}, {regions_.collectorLine, 0, 6, 1},
        {regions_.setLine, 0, 7, 2},        {regions_.collectorLine, 1, 6, 1},
        {regions_.setLine, 1, 7, 2},        {regions_.collectorBlock, 1, 6, 0},
    };
  }

  std::vector<OcrRead> reads;
  int used = 0;
  for (const auto &a : attempts) {
    if (used >= p_.maxAttemptsPerRead) break;
    const cv::Rect r = a.region & cv::Rect(0, 0, gray.cols, gray.rows);
    if (r.width < 8 || r.height < 6) continue;
    const int upscale = std::clamp(static_cast<int>(std::lround(260.0 / r.height)), 2, 16);
    cv::Mat prepared = prepare(gray(r), a.mode, upscale);
    const std::string text = run(prepared, a.psm);
    if (!debugDir_.empty()) {
      char name[512];
      std::snprintf(name, sizeof(name), "%s/%s_a%02d_m%d_p%d.png", debugDir_.c_str(),
                    debugPrefix_.c_str(), used, a.mode, a.psm);
      cv::imwrite(name, prepared);
      lastRawLog_ += "[" + std::string(name) + "]\n" + text + "\n";
    }
    ++used;
    if (text.find_first_not_of(" \n\r\t") == std::string::npos) continue;
    OcrRead parsed = parseCollectorText(text);
    parsed.regionId = a.regionId;
    reads.push_back(parsed);
  }

  OcrRead merged = mergeReads(reads);
  if (!merged.setCode.empty()) merged.setCode = snapSetCode(merged.setCode, p_.knownSetCodes);
  merged.score = scoreRead(merged);
  if (!debugDir_.empty()) {
    std::string log = lastRawLog_;
    log += "\nMERGED: " + merged.display() + " score=" + std::to_string(merged.score) + "\n";
    std::ofstream out(debugDir_ + "/" + debugPrefix_ + "_raw.txt");
    if (out) out << log;
  }
  return merged;
}

std::string CardOcr::readLineVariant(const cv::Mat &line, int mode, int psm, int upscale) {
  if (!api_ || line.empty()) return {};
  cv::Mat gray;
  if (line.channels() == 3) {
    cv::cvtColor(line, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = line;
  }
  return run(prepare(gray, mode, upscale), psm);
}

std::string CardOcr::readTitle(const cv::Mat &cardWarped) {
  if (!api_ || cardWarped.empty()) return {};
  cv::Mat gray;
  if (cardWarped.channels() == 3) {
    cv::cvtColor(cardWarped, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = cardWarped;
  }
  const cv::Rect r = regions_.title & cv::Rect(0, 0, gray.cols, gray.rows);
  if (r.width < 8 || r.height < 6) return {};
  std::string text = run(prepare(gray(r), 1, p_.upscale), 7);
  std::string cleaned;
  for (char c : text) {
    const unsigned char u = static_cast<unsigned char>(c);
    if (u < 0x20 && c != ' ') continue;
    if (u >= 0x80) return {};
    cleaned.push_back(c);
  }
  while (!cleaned.empty() && cleaned.front() == ' ') cleaned.erase(cleaned.begin());
  while (!cleaned.empty() && (cleaned.back() == ' ' || cleaned.back() == '\n')) cleaned.pop_back();
  if (cleaned.size() < 3) return {};
  int letters = 0;
  for (char c : cleaned) {
    if (std::isalpha(static_cast<unsigned char>(c))) ++letters;
  }
  if (letters < 3 || letters < static_cast<int>(cleaned.size()) * 0.5) return {};
  return cleaned;
}

}  // namespace cardscan
