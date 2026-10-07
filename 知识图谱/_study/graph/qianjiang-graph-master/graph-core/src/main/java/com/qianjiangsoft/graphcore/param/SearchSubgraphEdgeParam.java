package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class SearchSubgraphEdgeParam {

    EdgeType edgeType;

    Condition strengthCondition;

    Condition countCondition;

    SortingParam sorting;
}
