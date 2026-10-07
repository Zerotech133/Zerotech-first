package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/6/16 14:38
 */
@Data
public class FilterVertexParam {
    EdgeType edgeType;
    Condition strengthCondition;
    Condition countCondition;
    Condition outDegreeCondition;
    Condition inDegreeCondition;
    Condition allDegreeCondition;
    Condition propertyCondition;
    String tagName;
    String keyword;
}
