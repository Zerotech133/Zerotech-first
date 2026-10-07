package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.Edge;
import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.param.AnalyseFlowDirectionParam;
import com.qianjiangsoft.graphcore.param.ExtendGraphDataParam;
import com.qianjiangsoft.graphcore.param.SearchSubgraphParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 15:12
 */
public interface GraphAnalyseService {

    List<MergeEdge> searchSubgraph(SearchSubgraphParam searchSubgraphParam);

    List<MergeEdge> extendGraphDataV2(ExtendGraphDataParam extendGraphDataParam);

    List<Edge> findSNAVertexByActivity();

    List<Edge> findSNAVertexByCentrality();

    List<Edge> findSNAVertexByImportance();

    List<Edge> findStrongestCommunity();

    List<MergeEdge> analyseFlowDirection(AnalyseFlowDirectionParam param);
}
