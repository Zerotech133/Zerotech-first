package com.qianjiangsoft.graphapi.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import lombok.Data;

import java.util.Date;

/**
 * @author ulmarr8861
 * @date 2022/9/5
 */
@Data
public class SearchHistoryDTO implements Comparable<SearchHistoryDTO> {

    private String id;
    private String name;

    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createTime;

    private String param;

    private String type;

    private String spaceId;





    @Override
    public int compareTo(SearchHistoryDTO o) {
        Date date = o.getCreateTime();
        if (date.getTime() > this.getCreateTime().getTime()) {
            return 1;
        } else if (date.getTime() < this.getCreateTime().getTime()) {
            return -1;
        } else {
            return 0;
        }
    }
}
