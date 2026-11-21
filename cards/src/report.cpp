#include "report.h"

#include <cstdio>
#include <sstream>
#include <string>

namespace cardscan {
namespace {

std::string escape(const std::string &s) {
  std::string out;
  out.reserve(s.size() + 8);
  for (char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
          out += buf;
        } else {
          out.push_back(c);
        }
    }
  }
  return out;
}

std::string quoted(const std::string &s) { return "\"" + escape(s) + "\""; }

std::string optionalString(const std::string &s) { return s.empty() ? "null" : quoted(s); }

std::string num(double v, int precision = 3) {
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.*f", precision, v);
  return buf;
}

std::string cardJson(const CardResult &c, bool withTimeline) {
  std::ostringstream os;
  os << "    {\n";
  os << "      \"track\": " << c.trackId << ",\n";
  os << "      \"set\": " << optionalString(c.setCode) << ",\n";
  os << "      \"collector_number\": " << optionalString(c.number) << ",\n";
  os << "      \"collector_total\": " << optionalString(c.total) << ",\n";
  os << "      \"collector\": ";
  if (c.number.empty()) {
    os << "null";
  } else {
    os << quoted(c.total.empty() ? c.number : c.number + "/" + c.total);
  }
  os << ",\n";
  os << "      \"language\": " << optionalString(c.language) << ",\n";
  os << "      \"rarity\": " << optionalString(c.rarity) << ",\n";
  os << "      \"name\": " << optionalString(c.name) << ",\n";
  os << "      \"identity\": " << optionalString(c.identity) << ",\n";
  std::string identifiedBy = c.identifiedBy;
  if (identifiedBy.empty() && c.identified) identifiedBy = c.identifiedByCodes ? "codes" : "name";
  os << "      \"identified\": " << (c.identified ? "true" : "false") << ",\n";
  os << "      \"identified_by\": "
     << (identifiedBy.empty() ? std::string("null") : "\"" + identifiedBy + "\"") << ",\n";
  os << "      \"confidence\": " << num(c.confidence) << ",\n";
  os << "      \"quality\": " << num(c.quality) << ",\n";
  os << "      \"hits\": " << c.hits << ",\n";
  if (withTimeline) {
    os << "      \"first_frame\": " << c.firstFrame << ",\n";
    os << "      \"best_frame\": " << c.bestFrame << ",\n";
    os << "      \"last_frame\": " << c.lastFrame << ",\n";
    os << "      \"first_ts\": " << num(c.firstTs) << ",\n";
    os << "      \"best_ts\": " << num(c.bestTs) << ",\n";
    os << "      \"last_ts\": " << num(c.lastTs) << ",\n";
  }
  os << "      \"phash\": " << optionalString(c.hash) << ",\n";
  os << "      \"merged_tracks\": [";
  for (size_t i = 0; i < c.mergedTrackIds.size(); ++i) {
    if (i) os << ", ";
    os << c.mergedTrackIds[i];
  }
  os << "],\n";
  os << "      \"quad\": [";
  for (size_t i = 0; i < c.quad.size(); ++i) {
    if (i) os << ", ";
    os << "[" << num(c.quad[i].x, 1) << ", " << num(c.quad[i].y, 1) << "]";
  }
  os << "]\n";
  os << "    }";
  return os.str();
}

std::string cardsArray(const std::vector<CardResult> &cards, bool withTimeline) {
  std::ostringstream os;
  os << "[";
  for (size_t i = 0; i < cards.size(); ++i) {
    if (i) os << ",";
    os << "\n" << cardJson(cards[i], withTimeline);
  }
  if (!cards.empty()) os << "\n  ";
  os << "]";
  return os.str();
}

std::pair<int, int> countIdentified(const std::vector<CardResult> &cards) {
  int identified = 0;
  for (const auto &c : cards) {
    if (c.identified) ++identified;
  }
  return {identified, static_cast<int>(cards.size()) - identified};
}

}  // namespace

std::string imageResultJson(const ImageResult &result) {
  const auto counts = countIdentified(result.cards);
  std::ostringstream os;
  os << "{\n";
  os << "  \"input\": " << quoted(result.input) << ",\n";
  os << "  \"type\": \"image\",\n";
  os << "  \"width\": " << result.width << ",\n";
  os << "  \"height\": " << result.height << ",\n";
  os << "  \"count\": " << result.cards.size() << ",\n";
  os << "  \"identified\": " << counts.first << ",\n";
  os << "  \"unidentified\": " << counts.second << ",\n";
  os << "  \"cards\": " << cardsArray(result.cards, false) << "\n";
  os << "}\n";
  return os.str();
}

std::string videoResultJson(const VideoResult &result) {
  const auto counts = countIdentified(result.cards);
  std::ostringstream os;
  os << "{\n";
  os << "  \"input\": " << quoted(result.input) << ",\n";
  os << "  \"type\": \"video\",\n";
  os << "  \"width\": " << result.width << ",\n";
  os << "  \"height\": " << result.height << ",\n";
  os << "  \"fps\": " << num(result.fps) << ",\n";
  os << "  \"duration\": " << num(result.duration) << ",\n";
  os << "  \"frames\": " << result.frames << ",\n";
  os << "  \"processed_frames\": " << result.processedFrames << ",\n";
  os << "  \"ocr_reads\": " << result.readsTotal << ",\n";
  os << "  \"ocr_reads_with_identity\": " << result.readsWithIdentity << ",\n";
  os << "  \"elapsed_seconds\": " << num(result.elapsedSeconds, 2) << ",\n";
  os << "  \"count\": " << result.cards.size() << ",\n";
  os << "  \"identified\": " << counts.first << ",\n";
  os << "  \"unidentified\": " << counts.second << ",\n";
  os << "  \"cards\": " << cardsArray(result.cards, true) << "\n";
  os << "}\n";
  return os.str();
}

std::string formatImageSummary(const ImageResult &result) {
  const auto counts = countIdentified(result.cards);
  std::ostringstream os;
  os << result.input << ": " << result.cards.size() << " card(s), " << counts.first
     << " identified, " << counts.second << " unidentified\n";
  int index = 1;
  for (const auto &c : result.cards) {
    os << "  " << index++ << ". ";
    if (!c.identity.empty()) {
      os << c.setCode << " " << c.number;
      if (!c.total.empty()) os << "/" << c.total;
      if (!c.rarity.empty()) os << " " << c.rarity;
      if (!c.language.empty()) os << " " << c.language;
    } else {
      os << "?";
    }
    if (!c.name.empty()) os << "  \"" << c.name << "\"";
    os << "  conf=" << num(c.confidence, 2) << " q=" << num(c.quality, 2) << "\n";
  }
  return os.str();
}

std::string formatVideoSummary(const VideoResult &result) {
  const auto counts = countIdentified(result.cards);
  std::ostringstream os;
  os << result.input << ": " << result.processedFrames << "/" << result.frames
     << " frames processed, " << result.cards.size() << " card(s), " << counts.first
     << " identified, " << counts.second << " unidentified, " << num(result.elapsedSeconds, 1)
     << "s\n";
  int index = 1;
  for (const auto &c : result.cards) {
    os << "  " << index++ << ". [" << num(c.firstTs, 2) << "s";
    if (c.mergedTrackIds.size() > 1) os << ", " << c.mergedTrackIds.size() << " tracks";
    os << "] ";
    if (!c.identity.empty() && !c.setCode.empty()) {
      os << c.setCode << " " << c.number;
      if (!c.total.empty()) os << "/" << c.total;
      if (!c.rarity.empty()) os << " " << c.rarity;
      if (!c.language.empty()) os << " " << c.language;
    } else {
      os << "unknown";
    }
    if (!c.name.empty()) os << "  \"" << c.name << "\"";
    os << "  conf=" << num(c.confidence, 2) << " hits=" << c.hits << "\n";
  }
  return os.str();
}

}  // namespace cardscan
