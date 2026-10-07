package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class SearchSubgraphParam {

    List<Vertex> startPointList;

    Long maxStep;

    List<SearchSubgraphEdgeParam> edgeParamList;
}
