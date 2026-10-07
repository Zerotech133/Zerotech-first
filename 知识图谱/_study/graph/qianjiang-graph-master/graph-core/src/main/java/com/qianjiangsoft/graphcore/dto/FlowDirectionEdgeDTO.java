package com.qianjiangsoft.graphcore.dto;

import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.AllArgsConstructor;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/2/23 16:37
 */
@AllArgsConstructor
@Data
public class FlowDirectionEdgeDTO {

    String id;
    String name;
    Vertex source;
    Vertex destination;
    EdgeType type;

    /** 强度 */
    Double strength;
}
