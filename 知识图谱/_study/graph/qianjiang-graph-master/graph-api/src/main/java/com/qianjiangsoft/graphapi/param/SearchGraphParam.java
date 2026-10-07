package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.param.Condition;
import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/3/2 16:56
 */
@Data
public class SearchGraphParam {
    Condition propertyCondition;

    List<EdgeType> edgeType;

    Condition timeCondition;

    Condition strengthCondition;

    Condition degreeCondition;
}
