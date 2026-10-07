package com.qianjiangsoft.graphcore.param;

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
public class MergeEdgeParam {
    Vertex source;
    Vertex destination;
    EdgeType type;
}
