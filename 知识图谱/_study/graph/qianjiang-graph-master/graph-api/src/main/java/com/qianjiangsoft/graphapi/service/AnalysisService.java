package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.*;
import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphcore.param.TextSearchParam;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/18
 */
public interface AnalysisService {

    GraphCommonReturnDTO extendGraph(GraphExtendParam param);

    GraphPathsReturnDTO graphFindPath(GraphExploreParam param);

    GraphCommonReturnDTO searchSubgraph(SearchSubGraphParam param);

    GraphCommonReturnDTO complexSearch(GraphComplexSearch param);

    GraphPathsReturnDTO graphFindLoop(GraphFindLoopParam param);

    GraphCommonReturnDTO textSearch(TextSearchParam param);

    GraphCommonReturnDTO graphStatistics(GraphStatisticsParam param);

    List<VertexReturnDTO> searchVertexListByName(searchVertexListByNameParam param);

    List<EdgeReturnDTO> queryEdgeByMergeEdge(MergeEdgeConvertParam param);

    GraphCommonReturnDTO analyseFlowDirection(FlowDirectionParam flowDirectionParam);

    void exportExcel(MergeEdgeConvertParam param);

    GraphCommonReturnDTO searchSameTagRelation(SearchSameTagRelationParam param);
}
