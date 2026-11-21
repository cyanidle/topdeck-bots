#pragma once

#include <string>

#include <opencv2/core.hpp>

namespace cardscan {

std::string hashToHex(const cv::Mat &hash);
int hashDistance(const std::string &a, const std::string &b);
std::string perceptualHash(const cv::Mat &crop);
std::string colorSignature(const cv::Mat &crop);
double signatureDistance(const std::string &a, const std::string &b);

}
