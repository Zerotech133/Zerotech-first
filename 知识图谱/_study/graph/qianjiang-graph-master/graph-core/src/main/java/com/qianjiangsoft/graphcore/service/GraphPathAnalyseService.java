package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.param.SearchAllPathParam;
import com.qianjiangsoft.graphcore.param.SearchLoopPathParam;
import com.qianjiangsoft.graphcore.param.SearchSameTagPathParam;
import com.qianjiangsoft.graphcore.param.SearchShortestPathParam;

import java.util.List;
import java.util.Set;

/**
 * @author marshallwu
 * @date 2022/4/28 11:46
 */
public interface GraphPathAnalyseService {

    List<List<MergeEdge>> searchLoopPath(SearchLoopPathParam searchLoopPathParam);

    List<List<MergeEdge>> searchAllPath(SearchAllPathParam searchAllPathParam);

    List<List<MergeEdge>> searchShortestPath(SearchShortestPathParam searchShortestPathParam);

    List <MergeEdge> searchSameTagPath(SearchSameTagPathParam searchAllPathParam);
}
