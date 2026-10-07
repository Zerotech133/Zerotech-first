package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.param.CreateTagParam;
import com.qianjiangsoft.graphapi.param.UpdateTagParam;
import com.qianjiangsoft.graphapi.service.TagService;
import com.qianjiangsoft.graphcore.model.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/18
 */
@RequestMapping("/tag")
@RestController
@RequiredArgsConstructor
public class TagController {

    private final TagService tagService;

    @GetMapping("/select/bySpaceId")
    public List<Tag> selectTagListBySpaceId(@RequestParam("spaceId") String spaceId){
        return tagService.selectBySpaceId(spaceId);
    }
    @PostMapping("/create")
    public void createTag(@RequestBody CreateTagParam param) {
        tagService.create(param);
    }

    @PostMapping("/delete")
    public void delete(@RequestParam("id") String id) {
        tagService.delete(id);
    }

    @PostMapping("/update")
    public void update(@RequestBody UpdateTagParam param) {
        tagService.update(param);
    }

    @PostMapping("/batchCreate")
    public void createBatchTags(@RequestBody List<CreateTagParam> tagList) {
        tagService.createBatchTags(tagList);
    }

    @PostMapping("/deleteAllTag")
    public void deleteAllTag() {
        tagService.deleteAllTag();
    }


}
