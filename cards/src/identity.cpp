#include "identity.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <functional>
#include <map>
#include <vector>

#include <ctre.hpp>

namespace cardscan {
namespace {

const char *kLanguages[] = {"EN", "RU", "DE", "FR", "IT", "ES", "PT", "JA", "JP", "KO",
                            "KR", "ZH", "ZHS", "ZHT", "CN", "TW", "SP", "PL", "NL",
                            "CS", "SK", "HU", "TR", "HE", "LA", "GR", "AR", "SA", "TH"};

bool isLanguage(const std::string &token) {
  for (const char *l : kLanguages) {
    if (token == l) return true;
  }
  return false;
}

std::string collapseSpaces(const std::string &s) {
  std::string out;
  out.reserve(s.size());
  bool prevSpace = false;
  for (char ch : s) {
    if (ch == '\n' || ch == '\r' || ch == '\t' || ch == ' ') {
      if (!out.empty() && !prevSpace) {
        out.push_back(' ');
        prevSpace = true;
      }
      continue;
    }
    out.push_back(ch);
    prevSpace = false;
  }
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

}  // namespace

std::string asciiUpper(const std::string &s) {
  std::string out = s;
  for (char &c : out) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  return out;
}

std::string normalizeOcrText(const std::string &s) {
  std::string ascii;
  ascii.reserve(s.size());
  for (unsigned char u : s) {
    if (u >= 0x80) continue;
    if (u < 0x20 && u != '\n' && u != '\t') continue;
    ascii.push_back(static_cast<char>(std::toupper(u)));
  }
  for (char &c : ascii) {
    if (c == '.' || c == ',' || c == ':' || c == '~' || c == '+' || c == '-' || c == '"' || c == '\'')
      c = '*';
    if (c == ';') c = '*';
    if (c == '=') c = ' ';
  }
  return collapseSpaces(ascii);
}

int scoreRead(const OcrRead &r) {
  int score = 0;
  if (!r.number.empty()) {
    score += r.total.empty() ? 3 : 4;
    if (!r.total.empty()) {
      try {
        const int num = std::stoi(r.number);
        const int total = std::stoi(r.total);
        if (total > 0 && num >= 1 && num <= total) score += 1;
      } catch (...) {
      }
    }
  }
  if (r.hasSetCode()) score += 3;
  if (!r.language.empty()) score += 2;
  if (!r.rarity.empty()) score += 1;
  if (r.hasIdentity() && !r.language.empty()) score += 1;
  if (!r.name.empty()) score += 1;
  return score;
}

bool betterRead(const OcrRead &candidate, const OcrRead &current) {
  return scoreRead(candidate) > scoreRead(current);
}

std::string OcrRead::identityKey() const {
  if (setCode.empty() || number.empty()) return {};
  return setCode + ":" + number;
}

std::string OcrRead::display() const {
  std::string out;
  if (!setCode.empty()) out += setCode;
  if (!number.empty()) {
    if (!out.empty()) out += " ";
    out += number;
    if (!total.empty()) out += "/" + total;
  }
  if (!rarity.empty()) out += " " + rarity;
  if (!language.empty()) out += " " + language;
  if (!name.empty()) {
    if (!out.empty()) out += " | ";
    out += name;
  }
  return out;
}

OcrRead parseCollectorText(const std::string &text) {
  OcrRead r;
  r.raw = text;
  const std::string s = fixDigitConfusions(normalizeOcrText(text));
  if (s.empty()) return r;

  if (auto m = ctre::search<R"((\d{1,4})\s*[/\\|I1L]\s*(\d{1,4})\s*([CURMSLTB])?)">(s)) {
    r.number = m.get<1>().to_string();
    r.total = m.get<2>().to_string();
    r.rarity = m.get<3>().to_string();
    try {
      const int number = std::stoi(r.number);
      const int total = std::stoi(r.total);
      if (total < 20 || number < 1 || number > total) {
        r.number.clear();
        r.total.clear();
        r.rarity.clear();
      }
    } catch (...) {
      r.number.clear();
      r.total.clear();
    }
  }

  // Many printings show no "/total": either "<rarity> <number> <code>" (Universes Beyond,
  // e.g. "U 0180 FFXVI") or the zero-padded number alone.
  if (r.number.empty()) {
    if (auto m = ctre::search<R"(\b([CURMSLTB])\s+(\d{3,4})\b)">(s)) {
      r.rarity = m.get<1>().to_string();
      r.number = m.get<2>().to_string();
    } else if (auto m = ctre::search<R"(\b(0\d{2,3})\b)">(s)) {
      r.number = m.get<1>().to_string();
    } else if (auto m = ctre::search<R"(\b(\d{3})\b)">(s)) {
      r.number = m.get<1>().to_string();
    }
  }

  if (auto m = ctre::search<R"(([A-Z][A-Z0-9]{1,5})[*\s]{1,4}(EN|RU|DE|FR|IT|ES|PT|JA|JP|KO|KR|ZH|ZHS|ZHT|CN|TW|SP|PL|NL|CS|SK|HU|TR|HE)\b)">(s)) {
    r.setCode = m.get<1>().to_string();
    r.language = m.get<2>().to_string();
  } else if (auto m = ctre::search<R"(\b([A-Z][A-Z0-9]{1,4})\s+(EN|RU|DE|FR|IT|ES|PT|JA|JP|KO|KR|ZH|ZHS|ZHT|CN|TW|SP|PL|NL|CS|SK|HU|TR|HE)\b)">(s)) {
    r.setCode = m.get<1>().to_string();
    r.language = m.get<2>().to_string();
  } else if (auto m = ctre::search<R"(^\s*([A-Z][A-Z0-9]{2,4})[*\s]{1,3}[A-Z]{2,3}\b)">(s)) {
    r.setCode = m.get<1>().to_string();
  }

  if (!r.setCode.empty() && isLanguage(r.setCode)) r.setCode.clear();
  if (r.setCode == "THE" || r.setCode == "AND" || r.setCode == "ART") r.setCode.clear();

  r.score = scoreRead(r);
  return r;
}

std::string fixDigitConfusions(const std::string &s) {
  auto digitish = [](char c) {
    return std::isdigit(static_cast<unsigned char>(c)) || c == '/' || c == '\\' || c == '|' ||
           c == '*';
  };
  auto isDigit = [](char c) { return std::isdigit(static_cast<unsigned char>(c)); };
  static const char *pairs[][2] = {{"OQ", "0"}, {"IL", "1"}, {"S", "5"},
                                   {"B", "8"},  {"Z", "2"}, {"G", "6"}};
  std::string out = s;
  for (size_t i = 0; i < s.size(); ++i) {
    char replacement = 0;
    for (const auto &p : pairs) {
      if (std::strchr(p[0], s[i])) {
        replacement = p[1][0];
        break;
      }
    }
    if (replacement == 0) continue;
    size_t left = i;
    while (left > 0 && s[left - 1] == ' ') --left;
    size_t right = i;
    while (right + 1 < s.size() && s[right + 1] == ' ') ++right;
    const bool leftOk = left > 0 && digitish(s[left - 1]);
    const bool rightOk = right + 1 < s.size() && digitish(s[right + 1]);
    const bool hasDigit = (left > 0 && isDigit(s[left - 1])) ||
                          (right + 1 < s.size() && isDigit(s[right + 1]));
    if (leftOk && rightOk && hasDigit) out[i] = replacement;
  }
  return out;
}

double codeDistance(const std::string &a, const std::string &b) {
  static const char *confusable[] = {"O0DQ", "I1LJT", "S5", "B8",  "Z2", "G6C",
                                     "A4",   "E3",    "UYV", "MNW", "RK", "P9", "F7"};
  auto substitutionCost = [&](char x, char y) {
    if (x == y) return 0.0;
    for (const char *group : confusable) {
      if (std::strchr(group, x) && std::strchr(group, y)) return 0.35;
    }
    return 1.0;
  };
  const size_t n = a.size(), m = b.size();
  std::vector<double> prev(m + 1), cur(m + 1);
  for (size_t j = 0; j <= m; ++j) prev[j] = static_cast<double>(j);
  for (size_t i = 1; i <= n; ++i) {
    cur[0] = static_cast<double>(i);
    for (size_t j = 1; j <= m; ++j) {
      cur[j] = std::min({prev[j] + 1.0, cur[j - 1] + 1.0,
                         prev[j - 1] + substitutionCost(a[i - 1], b[j - 1])});
    }
    prev.swap(cur);
  }
  return prev[m];
}

std::string snapSetCode(const std::string &code, const std::vector<std::string> &known) {
  if (code.empty() || known.empty()) return code;
  std::string best;
  double bestDistance = 1e9;
  double secondDistance = 1e9;
  for (const auto &candidate : known) {
    if (candidate.empty()) continue;
    if (candidate == code) return candidate;
    const double d = codeDistance(code, candidate);
    if (d < bestDistance) {
      secondDistance = bestDistance;
      bestDistance = d;
      best = candidate;
    } else if (d < secondDistance) {
      secondDistance = d;
    }
  }
  if (!best.empty() && bestDistance <= 1.05 && secondDistance > bestDistance + 0.2) return best;
  return code;
}

OcrRead mergeReads(const std::vector<OcrRead> &reads) {
  auto pickBest = [](const std::vector<std::pair<std::string, double>> &items) {
    std::map<std::string, std::pair<int, double>> agg;
    for (const auto &item : items) {
      if (item.first.empty()) continue;
      auto &entry = agg[item.first];
      entry.first += 1;
      entry.second += item.second;
    }
    std::string best;
    int bestCount = 0;
    double bestWeight = 0.0;
    for (const auto &entry : agg) {
      if (entry.second.first > bestCount ||
          (entry.second.first == bestCount && entry.second.second > bestWeight)) {
        best = entry.first;
        bestCount = entry.second.first;
        bestWeight = entry.second.second;
      }
    }
    return best;
  };

  auto voteField = [&](int preferredRegion, bool requireLanguage,
                       const std::function<std::string(const OcrRead &)> &extract) {
    for (int region : {preferredRegion, 0, -1}) {
      std::vector<std::pair<std::string, double>> items;
      for (const auto &r : reads) {
        if (region >= 0 && r.regionId != region) continue;
        if (requireLanguage && r.language.empty()) continue;
        const std::string value = extract(r);
        if (value.empty()) continue;
        items.emplace_back(value, static_cast<double>(std::max(1, r.score)));
      }
      const std::string best = pickBest(items);
      if (!best.empty()) return best;
    }
    return std::string();
  };

  OcrRead out;
  const std::string pair = voteField(1, false, [](const OcrRead &r) {
    return r.number.empty() || r.total.empty() ? std::string() : r.number + "/" + r.total;
  });
  if (!pair.empty()) {
    const size_t slash = pair.find('/');
    if (slash != std::string::npos) {
      out.number = pair.substr(0, slash);
      out.total = pair.substr(slash + 1);
    }
  } else {
    out.number = voteField(1, false, [](const OcrRead &r) { return r.number; });
  }
  out.setCode = voteField(2, false, [](const OcrRead &r) { return r.setCode; });
  if (!out.number.empty()) {
    // The set code must belong to the same read as the number: a code read from one line
    // and a number read from another (or from a neighbouring card) made wrong identities.
    std::vector<std::pair<std::string, double>> items;
    for (const auto &r : reads) {
      if (r.number != out.number || r.setCode.empty()) continue;
      items.emplace_back(r.setCode, static_cast<double>(std::max(1, r.score)));
    }
    const std::string sameLine = pickBest(items);
    if (!sameLine.empty()) out.setCode = sameLine;
  }
  out.language = voteField(2, false,
                           [](const OcrRead &r) { return r.setCode.empty() ? std::string() : r.language; });
  out.rarity = voteField(1, false, [](const OcrRead &r) { return r.rarity; });
  out.score = scoreRead(out);
  for (const auto &r : reads) {
    if (!r.raw.empty()) {
      if (!out.raw.empty()) out.raw += " | ";
      out.raw += r.raw;
    }
  }
  return out;
}

}  // namespace cardscan
