package com.qianjiangsoft.graphapi.param;

import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
public class DeleteEdgeParam {
    String sourceVertexId;
    String destinationVertexId;
    String edgeTypeName;
}
