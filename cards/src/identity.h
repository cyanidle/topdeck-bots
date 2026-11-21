#pragma once

#include <string>
#include <vector>

namespace cardscan {

struct OcrRead {
  std::string number;    // "051"
  std::string total;     // "065"
  std::string setCode;   // "DDS"
  std::string language;  // "EN"
  std::string rarity;    // "C"
  std::string name;      // title fallback, Latin only for now
  std::string raw;       // exact text the read came from
  int regionId = -1;     // 0 = collector block, 1 = collector line, 2 = set line, 3 = title
  int score = 0;         // 0..12, see scoreRead()

  bool hasCollector() const { return !number.empty() && !total.empty(); }
  bool hasSetCode() const { return !setCode.empty(); }
  bool hasIdentity() const { return hasCollector() && hasSetCode(); }

  std::string identityKey() const;
  std::string display() const;
};

std::string asciiUpper(const std::string &s);
std::string normalizeOcrText(const std::string &s);
std::string fixDigitConfusions(const std::string &s);
OcrRead parseCollectorText(const std::string &text);
OcrRead mergeReads(const std::vector<OcrRead> &reads);
double codeDistance(const std::string &a, const std::string &b);
std::string snapSetCode(const std::string &code, const std::vector<std::string> &known);
int scoreRead(const OcrRead &r);
bool betterRead(const OcrRead &candidate, const OcrRead &current);

}
