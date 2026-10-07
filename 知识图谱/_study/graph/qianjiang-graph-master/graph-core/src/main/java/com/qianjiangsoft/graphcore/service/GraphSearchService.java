package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.dto.SearchResultDTO;
import com.qianjiangsoft.graphcore.param.ComplexSearchParam;
import com.qianjiangsoft.graphcore.param.StatisticsSearchParam;
import com.qianjiangsoft.graphcore.param.TextSearchParam;

/**
 * @author marshallwu
 * @date 2022/4/28 17:51
 */
public interface GraphSearchService {


    SearchResultDTO complexSearch(ComplexSearchParam searchParam);

    SearchResultDTO statisticsSearch(StatisticsSearchParam searchParam);

    SearchResultDTO textSearch(TextSearchParam searchParam);
}
