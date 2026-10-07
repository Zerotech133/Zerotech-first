package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.Objects;

/**
 * @author marshallwu
 * @date 2022/4/24 14:00
 */
@Data
public class MergeEdgeDTO {

    private String sourceId;

    private String destinationId;
    /**类型 */
    private String edgeTypeName;
    /**强度 */
    private Double strength;
    /**次数 */
    private Long count;

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        MergeEdgeDTO that = (MergeEdgeDTO) o;
        return sourceId.equals(that.sourceId) && destinationId.equals(that.destinationId) && edgeTypeName.equals(that.edgeTypeName);
    }

    @Override
    public int hashCode() {
        return Objects.hash(sourceId, destinationId, edgeTypeName);
    }
}
