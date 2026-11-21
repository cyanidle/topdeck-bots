#include "phash.h"

#include <cstdio>

#include <opencv2/img_hash.hpp>
#include <opencv2/imgproc.hpp>

namespace cardscan {

std::string hashToHex(const cv::Mat &hash) {
  std::string hex;
  hex.reserve(hash.total() * 2);
  char buf[4];
  for (int i = 0; i < static_cast<int>(hash.total()); ++i) {
    std::snprintf(buf, sizeof(buf), "%02x", hash.at<uchar>(i));
    hex += buf;
  }
  return hex;
}

int hashDistance(const std::string &a, const std::string &b) {
  if (a.empty() || b.empty() || a.size() != b.size()) return 1000;
  int distance = 0;
  for (size_t i = 0; i + 1 < a.size(); i += 2) {
    int x = 0, y = 0;
    std::sscanf(a.c_str() + i, "%2x", &x);
    std::sscanf(b.c_str() + i, "%2x", &y);
    distance += __builtin_popcount(static_cast<unsigned>(x ^ y));
  }
  return distance;
}

std::string perceptualHash(const cv::Mat &crop) {
  if (crop.empty()) return {};
  cv::Mat gray;
  if (crop.channels() == 3) {
    cv::cvtColor(crop, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = crop;
  }
  cv::Mat resized;
  cv::resize(gray, resized, cv::Size(64, 64), 0, 0, cv::INTER_AREA);
  cv::Mat hash;
  cv::img_hash::pHash(resized, hash);
  return hashToHex(hash);
}

std::string colorSignature(const cv::Mat &crop) {
  if (crop.empty()) return {};
  cv::Mat lab;
  cv::cvtColor(crop, lab, cv::COLOR_BGR2Lab);
  cv::Mat resized;
  cv::resize(lab, resized, cv::Size(8, 8), 0, 0, cv::INTER_AREA);
  std::string out;
  out.reserve(8 * 8 * 3);
  for (int y = 0; y < resized.rows; ++y) {
    for (int x = 0; x < resized.cols; ++x) {
      const cv::Vec3b &p = resized.at<cv::Vec3b>(y, x);
      for (int c = 0; c < 3; ++c) out.push_back(static_cast<char>(p[c] >> 1));
    }
  }
  return out;
}

double signatureDistance(const std::string &a, const std::string &b) {
  if (a.empty() || b.empty() || a.size() != b.size()) return 1e9;
  double sum = 0.0;
  for (size_t i = 0; i < a.size(); ++i) {
    const double d = static_cast<unsigned char>(a[i]) - static_cast<unsigned char>(b[i]);
    sum += d * d;
  }
  return std::sqrt(sum / (a.size() / 3));
}

}
