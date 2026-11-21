#pragma once

#include <opencv2/core.hpp>

namespace cardscan {

int countTextRuns(const cv::Mat &cardWarped, const cv::Rect &region);
int countTextRunsInGray(const cv::Mat &gray);

}
