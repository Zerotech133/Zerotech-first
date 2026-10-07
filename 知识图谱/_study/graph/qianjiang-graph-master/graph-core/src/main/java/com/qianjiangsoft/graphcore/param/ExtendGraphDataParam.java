package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.ExtendDirection;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
public class ExtendGraphDataParam {

    Vertex startPoint;
    List<EdgeType> edgeTypeList;
    Integer minStep;
    Integer maxStep;
    ExtendDirection direction;
    Condition timeCondition;
    Condition strengthCondition;
    Condition countCondition;
    SortingParam sorting;
}
