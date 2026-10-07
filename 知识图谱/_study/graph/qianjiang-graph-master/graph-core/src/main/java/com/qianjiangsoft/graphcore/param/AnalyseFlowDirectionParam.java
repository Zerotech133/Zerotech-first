package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/6/9 16:02
 */
@Data
public class AnalyseFlowDirectionParam {
    EdgeType edgeType;
    Condition strengthCondition;
}
